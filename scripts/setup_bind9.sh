#!/usr/bin/env bash
# Optional Bind9 authoritative DDNS setup (Rocky 9/10).
set -euo pipefail
fail(){ echo "[Bind9 오류] $*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || fail 'root 권한 필요'
[[ $# -ge 3 ]] || fail '사용법: setup_bind9.sh <forward-zone> <reverse-zone> <zone-ns-ip> [client-listen-ip cidr...]'
FORWARD="${1%.}"; REVERSE="${2%.}"; DNS_IP="$3"
CLIENT_IP="${4:-$DNS_IP}"
shift $(( $# >= 4 ? 4 : 3 ))
CLIENT_CIDRS=("$@")
python3 - "$FORWARD" "$REVERSE" "$DNS_IP" <<'PY'
import ipaddress,re,sys
f,r,ip=sys.argv[1:]
pattern=re.compile(r'^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$')
for z in (f,r):
    if len(z)>253 or not all(pattern.fullmatch(label) for label in z.split('.')):
        sys.exit('Invalid DNS zone: '+z)
if not r.endswith('.in-addr.arpa'): sys.exit('Reverse zone required')
ipaddress.IPv4Address(ip)
PY
python3 - "$CLIENT_IP" "${CLIENT_CIDRS[@]}" <<'PY'
import ipaddress,sys
ipaddress.IPv4Address(sys.argv[1])
for value in sys.argv[2:]:
    ipaddress.IPv4Network(value, strict=True)
PY
rpm -q bind >/dev/null 2>&1 || dnf install -y bind
command -v nsupdate >/dev/null || dnf install -y bind-utils
for cmd in named-checkconf named-checkzone tsig-keygen; do command -v "$cmd" >/dev/null || fail "missing: $cmd"; done
[[ -f /etc/named.conf ]] || fail '/etc/named.conf 없음'
getent group pkvm >/dev/null || fail 'KVM 서비스 설치 후 실행해야 합니다'
install -d -o root -g named -m 0750 /etc/named/pkvm
install -d -o named -g named -m 0750 /var/named/pkvm
KEY=/etc/named/pkvm/pkvm-ddns.key
if [[ ! -e "$KEY" ]]; then
    (umask 0077; tsig-keygen -a hmac-sha256 pkvm-ddns > "$KEY")
    chown root:named "$KEY"; chmod 0640 "$KEY"
fi
for zone in "$FORWARD" "$REVERSE"; do
    file="/var/named/pkvm/${zone}.zone"
    if [[ ! -e "$file" ]]; then
        cat > "$file" <<ZONE
\$TTL 300
@ IN SOA ns1.${FORWARD}. hostmaster.${FORWARD}. ( 1 3600 900 604800 300 )
@ IN NS ns1.${FORWARD}.
ZONE
        if [[ "$zone" == "$FORWARD" ]]; then printf 'ns1 IN A %s\n' "$DNS_IP" >> "$file"; fi
        chown named:named "$file"; chmod 0640 "$file"
    fi
    named-checkzone "$zone" "$file" >/dev/null || fail "zone 검사 실패: $zone"
done
CONF=/etc/named/pkvm/pkvm-zones.conf
if [[ -e "$CONF" ]]; then
    grep -Fq "zone \"${FORWARD}\"" "$CONF" || fail '기존 zone 설정 불일치'
    grep -Fq "zone \"${REVERSE}\"" "$CONF" || fail '기존 reverse 설정 불일치'
else
    cat > "$CONF" <<ZONE
include "/etc/named/pkvm/pkvm-ddns.key";
zone "${FORWARD}" IN { type master; file "/var/named/pkvm/${FORWARD}.zone"; update-policy { grant pkvm-ddns zonesub A; }; allow-transfer { none; }; };
zone "${REVERSE}" IN { type master; file "/var/named/pkvm/${REVERSE}.zone"; update-policy { grant pkvm-ddns zonesub PTR; }; allow-transfer { none; }; };
ZONE
    chown root:named "$CONF"; chmod 0640 "$CONF"
fi
if ! grep -Fq 'include "/etc/named/pkvm/pkvm-zones.conf";' /etc/named.conf; then
    cp -a /etc/named.conf "/etc/named.conf.pkvm-backup.$(date +%Y%m%d%H%M%S)"
    printf '\ninclude "/etc/named/pkvm/pkvm-zones.conf";\n' >> /etc/named.conf
fi
# Rocky's stock named.conf listens on localhost only. Keep DDNS local, but
# expose recursive/authoritative queries on the host bridge to configured VM
# networks. The TSIG key remains protected and is never served over DNS.
cp -a /etc/named.conf "/etc/named.conf.pkvm-client-backup.$(date +%Y%m%d%H%M%S)"
python3 - /etc/named.conf "$CLIENT_IP" "${CLIENT_CIDRS[@]}" <<'PY'
from pathlib import Path
import re,sys
path=Path(sys.argv[1]); client_ip=sys.argv[2]; cidrs=sys.argv[3:]
text=path.read_text(encoding='utf-8')
listen=f"listen-on port 53 {{ 127.0.0.1; {client_ip}; }};"
acl=" ".join(f"{cidr};" for cidr in cidrs)
query=f"allow-query     {{ localhost; {acl} }};"
text,count=re.subn(r'listen-on\s+port\s+53\s*\{[^}]*\};',listen,text,count=1)
if count != 1: raise SystemExit('named.conf listen-on directive not found')
text,count=re.subn(r'allow-query\s*\{[^}]*\};',query,text,count=1)
if count != 1: raise SystemExit('named.conf allow-query directive not found')
path.write_text(text,encoding='utf-8')
PY
named-checkconf -z /etc/named.conf >/dev/null || fail 'named 설정 검증 실패 (백업 복원 필요)'
usermod -aG named pkvm
systemctl enable --now named.service
systemctl restart named.service
systemctl is-active --quiet named.service || fail 'named 시작 실패'
echo "[Bind9] DNS zone 및 VM client resolver 구성 완료: ${CLIENT_IP}"
echo "[Bind9] TSIG: $KEY"
