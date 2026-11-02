#!/bin/bash
# scripts/setup_networks.sh : 4대 가상 네트워크(virbr0~virbr3) 충돌 없는 자동 구축
set -e
export LIBVIRT_DEFAULT_URI="qemu:///system"
PHYS_NIC="${1:-enp3s0}"
HOST_BR_IP="${2:-192.168.0.6/24}"
HOST_BR_GW="${3:-192.168.0.1}"
CONFIG_YAML="${4:-/etc/personal-kvm/config.yaml}"
if [ ! -f "${CONFIG_YAML}" ]; then echo "[오류] 네트워크 구성파일이 없습니다: ${CONFIG_YAML}" >&2; exit 1; fi
eval "$(python3 - "${CONFIG_YAML}" <<'PY'
import ipaddress, shlex, sys, yaml
data=yaml.safe_load(open(sys.argv[1], encoding='utf-8'))['networks']
for key, prefix in [('bridged','BR'),('nat','NAT'),('internal','INT'),('management','MGMT')]:
    item=data[key]; network=ipaddress.ip_network(item['cidr'])
    values={'NAME':item['net_name'],'BRIDGE':item['bridge_name'],'CIDR':item['cidr'],
            'ADDRESS':str(item.get('gateway') or next(network.hosts())),'PREFIX':str(network.prefixlen)}
    for field,value in values.items(): print(f'{prefix}_{field}={shlex.quote(value)}')
PY
)"

if ! ip link show "${PHYS_NIC}" >/dev/null 2>&1; then
    echo "[오류] 물리 NIC를 찾을 수 없습니다: ${PHYS_NIC}" >&2; exit 1
fi
python3 -c "import ipaddress; ipaddress.ip_interface('${HOST_BR_IP}'); ipaddress.ip_address('${HOST_BR_GW}')"
NETWORK_BACKUP="/var/lib/personal-kvm/install-state/nm-before-network"
mkdir -p "$(dirname "${NETWORK_BACKUP}")"
if [ ! -d "${NETWORK_BACKUP}" ]; then
    cp -a /etc/NetworkManager/system-connections "${NETWORK_BACKUP}"
fi
NETWORK_OK=0
restore_network_on_failure() {
    if [ "${NETWORK_OK}" -eq 0 ] && [ -d "${NETWORK_BACKUP}" ]; then
        rm -rf /etc/NetworkManager/system-connections
        cp -a "${NETWORK_BACKUP}" /etc/NetworkManager/system-connections
        nmcli connection reload 2>/dev/null || true
        nmcli device connect "${PHYS_NIC}" 2>/dev/null || true
    fi
}
trap restore_network_on_failure EXIT

echo "[네트워크 1/4] 기본 libvirt default 네트워크 정리..."
virsh -c qemu:///system net-destroy default 2>/dev/null || true
virsh -c qemu:///system net-autostart default --disable 2>/dev/null || true
virsh -c qemu:///system net-undefine default 2>/dev/null || true

echo "[네트워크 2/4] 브리지드 네트워크 (virbr0) 점검 및 구성..."
BRIDGE_PROFILE="${BR_BRIDGE}"
SLAVE_PROFILE="bridge-slave-${PHYS_NIC}"

# Reconcile existing profiles as well as creating new ones.  The previous
# implementation skipped this entire block as soon as a virbr0 interface
# existed, leaving the physical NIC on its standalone profile and virbr0 with
# no ports.
if nmcli connection show "${BRIDGE_PROFILE}" >/dev/null 2>&1; then
    nmcli connection modify "${BRIDGE_PROFILE}" \
        connection.interface-name "${BR_BRIDGE}" connection.autoconnect yes \
        ipv4.method manual ipv4.addresses "${HOST_BR_IP}" ipv4.gateway "${HOST_BR_GW}" \
        ipv4.dns "8.8.8.8 1.1.1.1" bridge.stp no
else
    nmcli connection add type bridge con-name "${BR_BRIDGE}" ifname "${BR_BRIDGE}" \
        ipv4.method manual ipv4.addresses "${HOST_BR_IP}" ipv4.gateway "${HOST_BR_GW}" \
        ipv4.dns "8.8.8.8 1.1.1.1" bridge.stp no autoconnect yes
fi

if nmcli connection show "${SLAVE_PROFILE}" >/dev/null 2>&1; then
    nmcli connection modify "${SLAVE_PROFILE}" \
        connection.interface-name "${PHYS_NIC}" connection.master "${BR_BRIDGE}" \
        connection.slave-type bridge connection.autoconnect yes \
        connection.autoconnect-priority 100
else
    nmcli connection add type ethernet slave-type bridge \
        con-name "${SLAVE_PROFILE}" ifname "${PHYS_NIC}" master "${BR_BRIDGE}" autoconnect yes
    nmcli connection modify "${SLAVE_PROFILE}" connection.autoconnect-priority 100
fi

# Prevent the standalone profile that owned the physical NIC before the
# conversion from winning the autoconnect race after a host reboot.
ACTIVE_PHYS_PROFILE="$(nmcli -g GENERAL.CONNECTION device show "${PHYS_NIC}" 2>/dev/null || true)"
if [ -n "${ACTIVE_PHYS_PROFILE}" ] && [ "${ACTIVE_PHYS_PROFILE}" != "--" ] \
        && [ "${ACTIVE_PHYS_PROFILE}" != "${SLAVE_PROFILE}" ]; then
    nmcli connection modify "${ACTIVE_PHYS_PROFILE}" \
        connection.autoconnect no connection.autoconnect-priority -100
fi

# Bringing up the slave deactivates any standalone profile currently owning
# the NIC and attaches it to the bridge.  The host IP remains on virbr0.
nmcli connection up "${BRIDGE_PROFILE}"
nmcli connection up "${SLAVE_PROFILE}"

if ! ip -o link show master "${BR_BRIDGE}" | grep -q "${PHYS_NIC}"; then
    echo "[오류] ${PHYS_NIC}가 ${BR_BRIDGE}의 물리 포트로 연결되지 않았습니다." >&2
    exit 1
fi
if ip -o -4 addr show dev "${PHYS_NIC}" | grep -q 'inet '; then
    echo "[오류] 호스트 IPv4가 ${PHYS_NIC}에 남아 있습니다. ${BR_BRIDGE}로 이동해야 합니다." >&2
    exit 1
fi

# 헬퍼 함수: libvirt 네트워크 또는 커널 브리지 충돌 없이 안전하게 생성
setup_libvirt_net() {
    local NET_NAME="$1"
    local BR_NAME="$2"
    local XML_FILE="$3"

    # 이미 동일한 브리지가 활성화되어 있고 정상 동작 중이면 건너뜀
    if virsh -c qemu:///system net-info "${NET_NAME}" >/dev/null 2>&1; then
        virsh -c qemu:///system net-destroy "${NET_NAME}" 2>/dev/null || true
        virsh -c qemu:///system net-undefine "${NET_NAME}" 2>/dev/null || true
    fi
    # 다른 libvirt 네트워크가 해당 브리지(virbr1~3)를 점유 중인지 확인 후 해제
    for net in $(virsh -c qemu:///system net-list --all --name 2>/dev/null); do
        if virsh -c qemu:///system net-dumpxml "${net}" 2>/dev/null | grep -q "bridge name='${BR_NAME}'"; then
            virsh -c qemu:///system net-destroy "${net}" 2>/dev/null || true
            virsh -c qemu:///system net-undefine "${net}" 2>/dev/null || true
        fi
    done
    # 잔존 커널 브리지 인터페이스가 남아 있으면 정리
    if ip link show "${BR_NAME}" >/dev/null 2>&1; then
        ip link set "${BR_NAME}" down 2>/dev/null || true
        ip link delete "${BR_NAME}" type bridge 2>/dev/null || true
    fi

    virsh -c qemu:///system net-define "${XML_FILE}"
    virsh -c qemu:///system net-start "${NET_NAME}"
    virsh -c qemu:///system net-autostart "${NET_NAME}"
    rm -f "${XML_FILE}"
}

echo "[네트워크 3/4] NAT(virbr1), Internal(virbr2), Management(virbr3) 구성..."
cat <<EOF > /tmp/nat-virbr1.xml
<network>
  <name>${NAT_NAME}</name>
  <forward mode='nat'><nat><port start='1024' end='65535'/></nat></forward>
  <bridge name='${NAT_BRIDGE}' stp='off' delay='0'/>
  <ip address='${NAT_ADDRESS}' prefix='${NAT_PREFIX}'/>
</network>
EOF
setup_libvirt_net "${NAT_NAME}" "${NAT_BRIDGE}" "/tmp/nat-virbr1.xml"

cat <<EOF > /tmp/int-virbr2.xml
<network>
  <name>${INT_NAME}</name>
  <bridge name='${INT_BRIDGE}' stp='off' delay='0'/>
  <ip address='${INT_ADDRESS}' prefix='${INT_PREFIX}'/>
</network>
EOF
setup_libvirt_net "${INT_NAME}" "${INT_BRIDGE}" "/tmp/int-virbr2.xml"

cat <<EOF > /tmp/mgmt-virbr3.xml
<network>
  <name>${MGMT_NAME}</name>
  <bridge name='${MGMT_BRIDGE}' stp='off' delay='0'/>
  <ip address='${MGMT_ADDRESS}' prefix='${MGMT_PREFIX}'/>
</network>
EOF
setup_libvirt_net "${MGMT_NAME}" "${MGMT_BRIDGE}" "/tmp/mgmt-virbr3.xml"

echo "[네트워크 4/4] 커널 포워딩 및 QEMU 브리지 권한 설정..."
cat <<EOF > /etc/sysctl.d/99-kvm-net.conf
net.ipv4.ip_forward = 1
net.bridge.bridge-nf-call-iptables = 0
net.bridge.bridge-nf-call-ip6tables = 0
net.bridge.bridge-nf-call-arptables = 0
EOF
sysctl --system >/dev/null 2>&1 || true

mkdir -p /etc/qemu /etc/qemu-kvm
echo "allow all" > /etc/qemu/bridge.conf
echo "allow all" > /etc/qemu-kvm/bridge.conf
chmod 0644 /etc/qemu/bridge.conf /etc/qemu-kvm/bridge.conf
if [ -f /usr/libexec/qemu-bridge-helper ]; then
    chmod u+s /usr/libexec/qemu-bridge-helper
fi
if ! ping -c 1 -W 3 "${HOST_BR_GW}" >/dev/null 2>&1; then
    echo "[경고] 게이트웨이가 ICMP에 응답하지 않습니다. 경로와 링크 상태로 계속 검증합니다." >&2
    ip route get "${HOST_BR_GW}" >/dev/null
    ip link show up "${BR_BRIDGE}" >/dev/null
fi
# The KVM host must have an on-link route into EVERY guest network.
echo '[검증] KVM 호스트 -> 각 VM 서브넷 직접 경로 확인'
for ITEM in "${NAT_CIDR}:${NAT_BRIDGE}" "${INT_CIDR}:${INT_BRIDGE}" "${MGMT_CIDR}:${MGMT_BRIDGE}" "${BR_CIDR}:${BR_BRIDGE}"; do
    CIDR="${ITEM%%:*}"; BRIDGE="${ITEM#*:}"
    if ! ip -o -4 addr show dev "${BRIDGE}" | grep -q 'inet '; then
        echo "[오류] ${BRIDGE}에 호스트 IP가 없습니다. ${CIDR}의 VM에 접속할 수 없습니다." >&2
        exit 1
    fi
    echo "[정상] ${BRIDGE}: $(ip -o -4 addr show dev "${BRIDGE}" | awk '{print $4}') -> ${CIDR}"
done

NETWORK_OK=1
