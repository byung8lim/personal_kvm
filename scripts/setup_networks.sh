#!/usr/bin/env bash
# Rocky Linux 9.x/10.x: NetworkManager checkpoint protected physical-to-bridge migration.
set -euo pipefail
export LIBVIRT_DEFAULT_URI=qemu:///system

log() { printf '[네트워크] %s\n' "$*"; }
die() { printf '[오류] %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "필수 명령 없음: $1"; }
noninteractive_network_allowed() { [[ "${PKVM_ALLOW_NONINTERACTIVE_NETWORK:-0}" == 1 ]]; }

APPLY_BRIDGE=0
if [[ "${1:-}" == '--apply-bridge' ]]; then APPLY_BRIDGE=1; shift; fi
PHYS_NIC="${1:-enp1s0}"
HOST_BR_IP="${2:-192.168.0.40/24}"
HOST_BR_GW="${3:-192.168.0.1}"
CONFIG_YAML="${4:-/etc/personal-kvm/config.yaml}"
[[ $EUID -eq 0 ]] || die 'root 권한이 필요합니다.'
[[ -r "$CONFIG_YAML" ]] || die "설정 파일 없음: $CONFIG_YAML"
for cmd in nmcli ip python3 virsh modprobe sysctl systemctl; do need "$cmd"; done

# Emit only shell-quoted values, so YAML never becomes executable shell syntax.
NETWORK_VARIABLES="$(python3 - "$CONFIG_YAML" "$PHYS_NIC" "$HOST_BR_IP" "$HOST_BR_GW" <<'PY'
import ipaddress, shlex, sys, yaml
cfg, nic, host, gateway = sys.argv[1:]
with open(cfg, encoding='utf-8') as f:
    nets = yaml.safe_load(f)['networks']
br = nets['bridged']
assert nic == br['physical_nic'], 'CLI NIC differs from config.yaml'
assert ipaddress.ip_interface(host) == ipaddress.ip_interface(br['host_ip_cidr']), 'CLI IP differs from config.yaml'
assert ipaddress.ip_address(gateway) == ipaddress.ip_address(br['gateway']), 'CLI gateway differs from config.yaml'
assert ipaddress.ip_address(gateway) in ipaddress.ip_interface(host).network, 'Gateway is off-subnet'
for category, prefix in [('bridged','BR'),('nat','NAT'),('internal','INT'),('management','MGMT')]:
    item = nets[category]
    network = ipaddress.ip_network(item['cidr'], strict=True)
    address = item.get('gateway') or item.get('host_bind_ip') or str(next(network.hosts()))
    pairs = {'NAME': item['net_name'], 'BRIDGE': item['bridge_name'], 'CIDR': str(network),
             'ADDRESS': str(address), 'PREFIX': str(network.prefixlen)}
    for key, value in pairs.items():
        print(f'{prefix}_{key}={shlex.quote(str(value))}')
private = [ipaddress.ip_network(nets[k]['cidr']) for k in ('nat', 'internal', 'management')]
public = ipaddress.ip_network(br['cidr'])
assert all(not private[i].overlaps(private[j]) for i in range(3) for j in range(i+1,3)), 'Private network overlap'
assert all(not public.overlaps(n) for n in private), 'Private network overlaps public network'
PY
)" || die 'YAML / 인터페이스 / 주소 유효성 검사 실패'
eval "$NETWORK_VARIABLES"

[[ "$PHYS_NIC" != "$BR_BRIDGE" ]] || die '물리 NIC와 브리지 이름이 같습니다.'
ip link show dev "$PHYS_NIC" >/dev/null || die "물리 NIC 없음: $PHYS_NIC"
[[ "$(nmcli -g GENERAL.TYPE device show "$PHYS_NIC")" == ethernet ]] || die "$PHYS_NIC: NetworkManager 이더넷 장치가 아닙니다."

# Kernel bridge sysctls only work after module is explicitly loaded.
install -d -m 0755 /etc/modules-load.d /etc/sysctl.d
printf 'br_netfilter\n' > /etc/modules-load.d/99-personal-kvm-br-netfilter.conf
modprobe br_netfilter || die 'br_netfilter 모듈 적재 실패'
[[ -d /sys/module/br_netfilter ]] || die 'br_netfilter 모듈이 로드되지 않았습니다.'
cat > /etc/sysctl.d/99-kvm-net.conf <<'SYSCTL'
net.ipv4.ip_forward = 1
net.bridge.bridge-nf-call-iptables = 0
net.bridge.bridge-nf-call-ip6tables = 0
net.bridge.bridge-nf-call-arptables = 0
SYSCTL
sysctl -p /etc/sysctl.d/99-kvm-net.conf || die '브리지 sysctl 적용 실패'

BRIDGE_PROFILE="pkvm-bridge-${BR_BRIDGE}"
PORT_PROFILE="pkvm-port-${PHYS_NIC}"
ACTIVE_PHYS_PROFILE="$(nmcli -g GENERAL.CONNECTION device show "$PHYS_NIC" 2>/dev/null || true)"
# Preserve the original SSH-facing firewall zone when moving the host IP.
ORIGINAL_ZONE=""
if [[ -n "$ACTIVE_PHYS_PROFILE" && "$ACTIVE_PHYS_PROFILE" != '--' ]]; then
    ORIGINAL_ZONE="$(nmcli -g connection.zone connection show "$ACTIVE_PHYS_PROFILE" 2>/dev/null || true)"
fi

# Never destroy existing libvirt networks or unrelated kernel bridges.
for item in "$NAT_BRIDGE" "$INT_BRIDGE" "$MGMT_BRIDGE"; do
    if ip link show dev "$item" >/dev/null 2>&1; then
        owner_ok=0
        for name in "$NAT_NAME" "$INT_NAME" "$MGMT_NAME"; do
            [[ "$(virsh net-info "$name" 2>/dev/null | awk '/Bridge:/ {print $2}')" == "$item" ]] && owner_ok=1
        done
        [[ "$owner_ok" == 1 ]] || die "기존 $item 인터페이스는 다른 관리 주체 소유입니다. 자동 삭제하지 않습니다."
    fi
done

if [[ "$APPLY_BRIDGE" -eq 1 ]]; then
    # This child is run only inside NetworkManager's rollback checkpoint.
    if ! nmcli -g GENERAL.CONNECTION device show "$PHYS_NIC" | grep -Fxq "$PORT_PROFILE"; then
        if ! nmcli -t -f NAME connection show | grep -Fxq "$BRIDGE_PROFILE"; then
            nmcli connection add type bridge con-name "$BRIDGE_PROFILE" ifname "$BR_BRIDGE" \
                bridge.stp no ipv4.method manual ipv4.addresses "$HOST_BR_IP" \
                ipv4.gateway "$HOST_BR_GW" ipv4.dns '8.8.8.8 1.1.1.1' ipv6.method disabled
        else
            nmcli connection modify "$BRIDGE_PROFILE" connection.interface-name "$BR_BRIDGE" \
                ipv4.method manual ipv4.addresses "$HOST_BR_IP" ipv4.gateway "$HOST_BR_GW"
        fi
        if ! nmcli -t -f NAME connection show | grep -Fxq "$PORT_PROFILE"; then
            nmcli connection add type ethernet con-name "$PORT_PROFILE" ifname "$PHYS_NIC" \
                master "$BR_BRIDGE" slave-type bridge ipv4.method disabled ipv6.method disabled
        else
            nmcli connection modify "$PORT_PROFILE" connection.interface-name "$PHYS_NIC" \
                connection.master "$BR_BRIDGE" connection.slave-type bridge
        fi
        if [[ -n "$ORIGINAL_ZONE" && "$ORIGINAL_ZONE" != '--' ]]; then
            nmcli connection modify "$BRIDGE_PROFILE" connection.zone "$ORIGINAL_ZONE"
        fi
        nmcli connection up "$BRIDGE_PROFILE"
        nmcli connection up "$PORT_PROFILE"
    fi
    ip -o link show master "$BR_BRIDGE" | grep -q "$PHYS_NIC" || die '브리지 포트 연결 실패'
    ip -o -4 addr show dev "$BR_BRIDGE" | grep -Fq "$HOST_BR_IP" || die '브리지 IP 확인 실패'
    ip -4 route get "$HOST_BR_GW" | grep -Fq "dev $BR_BRIDGE" || die '게이트웨이 라우팅 검증 실패'
    [[ -z "$(ip -o -4 addr show dev "$PHYS_NIC")" ]] || die '물리 NIC에 기존 IP가 남아 있습니다.'
    log '새 브리지 링크/주소/라우트 확인 완료. 원격 운영자의 확정 승인이 필요합니다.'
    exit 0
fi

# A freshly installed libvirt may autostart its stock 'default' NAT network
# on virbr0. Retire ONLY that unused network, never arbitrary bridge owners.
if ip link show dev "$BR_BRIDGE" >/dev/null 2>&1 && \
   virsh net-info default >/dev/null 2>&1 && \
   [[ "$(virsh net-info default | awk '/Bridge:/ {print $2}')" == "$BR_BRIDGE" ]]; then
    if [[ -n "$(virsh list --all --name | sed '/^[[:space:]]*$/d')" ]]; then
        die "libvirt default 네트워크가 ${BR_BRIDGE}를 점유합니다. VM이 존재해 자동 제거하지 않습니다."
    fi
    if [[ ! -t 0 || ! -t 1 ]]; then
        noninteractive_network_allowed || die "기본 NAT 브리지 ${BR_BRIDGE} 해제에는 TTY 또는 PKVM_ALLOW_NONINTERACTIVE_NETWORK=1이 필요합니다."
    fi
    log "미사용 libvirt default 네트워크가 ${BR_BRIDGE} 점유: 기본 NAT만 정리"
    virsh net-destroy default >/dev/null 2>&1 || true
    virsh net-autostart default --disable >/dev/null 2>&1 || true
    virsh net-undefine default || die 'default 네트워크 정의 제거 실패'
fi

# If bridge already configured, check that the physical NIC really belongs to it.
if ip link show dev "$BR_BRIDGE" >/dev/null 2>&1; then
    if ! ip -o link show master "$BR_BRIDGE" | grep -q "$PHYS_NIC"; then
        die "$BR_BRIDGE 가 존재하지만 $PHYS_NIC 가 연결되지 않았습니다. 기존 브리지를 자동 교체하지 않습니다."
    fi
else
    for profile in "$BRIDGE_PROFILE" "$PORT_PROFILE"; do
        if nmcli -t -f NAME connection show | grep -Fxq "$profile"; then
            die "기존 연결 프로필 $profile 발견. 오작동 방지를 위해 자동 덮어쓰기를 중단합니다."
        fi
    done
    if [[ ! -t 0 || ! -t 1 ]]; then
        noninteractive_network_allowed || die '브리지 전환은 TTY 또는 PKVM_ALLOW_NONINTERACTIVE_NETWORK=1이 필요합니다.'
    fi
    # nmcli checks out a snapshot, executes a child, then asks for confirmation.
    # Loss of SSH / no explicit confirmation causes a timed rollback.
    log "${PHYS_NIC} -> ${BR_BRIDGE} 전환 예정 (최대 180초 내 승인 없으면 자동 복구)"
    log '승인 질문에 Yes를 입력해야만 연결 변경이 확정됩니다.'
    CHECKPOINT_COMMAND=(nmcli device checkpoint --timeout 180 --
        "$0" --apply-bridge "$PHYS_NIC" "$HOST_BR_IP" "$HOST_BR_GW" "$CONFIG_YAML")
    if noninteractive_network_allowed; then
        # Explicit automation opt-in: confirm only after the child validates
        # bridge membership, address, and the gateway route.
        printf 'Yes\n' | "${CHECKPOINT_COMMAND[@]}" || checkpoint_failed=1
    else
        "${CHECKPOINT_COMMAND[@]}" || checkpoint_failed=1
    fi
    if [[ "${checkpoint_failed:-0}" == 1 ]]; then
        # Checkpoint restores active device state; remove only our newly created profiles.
        nmcli connection delete "$PORT_PROFILE" >/dev/null 2>&1 || true
        nmcli connection delete "$BRIDGE_PROFILE" >/dev/null 2>&1 || true
        die '브리지 변경이 실패하거나 체크포인트가 롤백되었습니다.'
    fi
fi

# Defer standalone profile autoconnect modification until AFTER checkpoint confirmation.
if [[ -n "$ACTIVE_PHYS_PROFILE" && "$ACTIVE_PHYS_PROFILE" != '--' && "$ACTIVE_PHYS_PROFILE" != "$PORT_PROFILE" ]]; then
    nmcli connection modify "$ACTIVE_PHYS_PROFILE" connection.autoconnect no
fi
nmcli connection modify "$BRIDGE_PROFILE" connection.autoconnect yes
nmcli connection modify "$PORT_PROFILE" connection.autoconnect yes

setup_libvirt_net() {
    local net_name="$1" bridge="$2" xml="$3" actual
    if virsh net-info "$net_name" >/dev/null 2>&1; then
        actual="$(virsh net-info "$net_name" | awk '/Bridge:/ {print $2}')"
        [[ "$actual" == "$bridge" ]] || die "$net_name: 기존 브리지 $actual 와 요청 $bridge 가 다릅니다. 삭제하지 않습니다."
        log "$net_name($bridge): 기존 정의 유지"
        # Do not parse localized net-info labels. Read the full active-name
        # list so pipefail cannot turn an early grep exit into a false result.
        if ! virsh net-list --name | awk -v name="$net_name" \
            '$0 == name { found=1 } END { exit !found }'; then
            virsh net-start "$net_name"
        fi
    else
        # Fail on any other libvirt network using the requested bridge.
        while IFS= read -r other; do
            [[ -z "$other" ]] && continue
            if virsh net-dumpxml "$other" | grep -Eq "<bridge name=['\"]${bridge}['\"]"; then
                die "$bridge 는 기존 libvirt 네트워크 $other 가 사용 중입니다."
            fi
        done < <(virsh net-list --all --name)
        [[ ! -e "/sys/class/net/$bridge" ]] || die "$bridge: 기존 커널 인터페이스와 충돌"
        virsh net-define "$xml"
        virsh net-start "$net_name"
    fi
    virsh net-autostart "$net_name"
}

TMP_DIR="$(mktemp -d /tmp/pkvm-net.XXXXXXXX)"
trap 'rm -rf "$TMP_DIR"' EXIT
cat > "$TMP_DIR/nat.xml" <<XML
<network>
  <name>${NAT_NAME}</name>
  <forward mode='nat'><nat><port start='1024' end='65535'/></nat></forward>
  <bridge name='${NAT_BRIDGE}' stp='off' delay='0'/>
  <ip address='${NAT_ADDRESS}' prefix='${NAT_PREFIX}'/>
</network>
XML
cat > "$TMP_DIR/internal.xml" <<XML
<network>
  <name>${INT_NAME}</name>
  <bridge name='${INT_BRIDGE}' stp='off' delay='0'/>
  <ip address='${INT_ADDRESS}' prefix='${INT_PREFIX}'/>
</network>
XML
cat > "$TMP_DIR/management.xml" <<XML
<network>
  <name>${MGMT_NAME}</name>
  <bridge name='${MGMT_BRIDGE}' stp='off' delay='0'/>
  <ip address='${MGMT_ADDRESS}' prefix='${MGMT_PREFIX}'/>
</network>
XML
setup_libvirt_net "$NAT_NAME" "$NAT_BRIDGE" "$TMP_DIR/nat.xml"
setup_libvirt_net "$INT_NAME" "$INT_BRIDGE" "$TMP_DIR/internal.xml"
setup_libvirt_net "$MGMT_NAME" "$MGMT_BRIDGE" "$TMP_DIR/management.xml"

# qemu:///system normally uses libvirt, not the setuid bridge helper.
# Never grant blanket `allow all` or alter executable setuid bits.
HELPER=/usr/libexec/qemu-bridge-helper
if [[ -x "$HELPER" ]]; then
    install -d -m 0755 /etc/qemu
    printf 'allow %s\n' "$BR_BRIDGE" > /etc/qemu/bridge.conf
    chmod 0644 /etc/qemu/bridge.conf
    log "qemu-bridge-helper 확인: $HELPER (허용 브리지: $BR_BRIDGE)"
else
    log 'qemu-bridge-helper가 없어 setuid/ACL 변경 없이 libvirt 시스템 연결을 사용합니다.'
fi
if systemctl is-active --quiet firewalld; then
    need firewall-cmd
    firewall-cmd --state >/dev/null || die 'firewalld 서비스와 명령 연결 실패'
    # libvirt manages its own NAT/firewall zone; do not overwrite its zone/rules.
    if ! firewall-cmd --get-zones | tr ' ' '\n' | grep -qx libvirt; then
        log '주의: firewalld libvirt zone 미발견. NAT 통신은 실제 VM으로 추가 확인 필요.'
    fi
fi
for item in "$BR_BRIDGE" "$NAT_BRIDGE" "$INT_BRIDGE" "$MGMT_BRIDGE"; do
    ip -o -4 addr show dev "$item" | grep -q 'inet ' || die "$item: 호스트 IPv4 주소 확인 실패"
done
log '브리지/NAT/내부망/관리망 구성 완료'
