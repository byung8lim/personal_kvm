#!/usr/bin/env bash
# Route host DNS through the local Bind9 service without changing other links.
set -euo pipefail

fail() { echo "[Host DNS 오류] $*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || fail 'root 권한이 필요합니다.'
CONFIG="${1:-/etc/personal-kvm/config.yaml}"
[[ -r "$CONFIG" ]] || fail "설정 파일 없음: $CONFIG"
for cmd in nmcli dig python3; do command -v "$cmd" >/dev/null || fail "필수 명령 없음: $cmd"; done

mapfile -t VALUES < <(python3 - "$CONFIG" <<'PY'
import sys,yaml
with open(sys.argv[1],encoding='utf-8') as stream: data=yaml.safe_load(stream) or {}
print(data['networks']['bridged']['bridge_name'])
print((data.get('dns') or {}).get('forward_zone') or '')
for item in data['networks'].values(): print(item['bridge_name'])
PY
)
BRIDGE="${VALUES[0]}"
ZONE="${VALUES[1]}"
[[ -n "$ZONE" ]] || fail 'dns.forward_zone 설정이 없습니다.'
PROFILE="$(nmcli -g GENERAL.CONNECTION device show "$BRIDGE" 2>/dev/null || true)"
[[ -n "$PROFILE" && "$PROFILE" != '--' ]] || fail "$BRIDGE 활성 연결 프로필을 찾을 수 없습니다."

# Do not point the host at a resolver until both authoritative and recursive
# paths are live. `dig` exits zero for SERVFAIL, so verify the DNS status too.
dns_ok() { dig +time=3 +tries=1 "$@" | grep -q 'status: NOERROR'; }
dns_ok @127.0.0.1 "$ZONE" SOA || fail '로컬 권한 DNS 검증 실패'
dns_ok @127.0.0.1 . NS || fail '로컬 재귀 DNS 검증 실패'

# Permit DNS only on the zones that own Personal KVM bridges. This covers both
# UDP and TCP/53 and does not expose unrelated host interfaces.
if systemctl is-active --quiet firewalld; then
    declare -A DNS_ZONES=()
    for bridge in "${VALUES[@]:2}"; do
        zone="$(firewall-cmd --get-zone-of-interface="$bridge" 2>/dev/null || true)"
        [[ -n "$zone" ]] || zone="$(firewall-cmd --get-default-zone)"
        DNS_ZONES["$zone"]=1
    done
    for zone in "${!DNS_ZONES[@]}"; do
        firewall-cmd --permanent --zone="$zone" --add-service=dns >/dev/null
        firewall-cmd --zone="$zone" --add-service=dns >/dev/null
    done
fi

STATE=/var/lib/personal-kvm/install-state
install -d -m 0700 "$STATE"
if [[ ! -e "$STATE/host-dns.before" ]]; then
    {
        printf 'profile=%q\n' "$PROFILE"
        printf 'dns=%q\n' "$(nmcli -g ipv4.dns connection show "$PROFILE")"
        printf 'ignore_auto_dns=%q\n' "$(nmcli -g ipv4.ignore-auto-dns connection show "$PROFILE")"
    } > "$STATE/host-dns.before"
    chmod 0600 "$STATE/host-dns.before"
fi

nmcli connection modify "$PROFILE" ipv4.ignore-auto-dns yes ipv4.dns '127.0.0.1 8.8.8.8 1.1.1.1'
nmcli device reapply "$BRIDGE" >/dev/null
# Rocky 10.2 NetworkManager updates resolv.conf asynchronously after reapply.
resolver_applied=0
for _attempt in {1..20}; do
    if grep -Eq '^nameserver[[:space:]]+127\.0\.0\.1([[:space:]]|$)' /etc/resolv.conf; then
        resolver_applied=1
        break
    fi
    sleep 1
done
[[ "$resolver_applied" -eq 1 ]] || fail '/etc/resolv.conf에 로컬 DNS가 적용되지 않았습니다.'
dns_ok "$ZONE" SOA || fail '호스트 기본 resolver의 관리 zone 조회 실패'
echo "[Host DNS] $PROFILE/$BRIDGE -> 127.0.0.1 적용 완료"
