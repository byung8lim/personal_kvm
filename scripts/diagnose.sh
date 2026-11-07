#!/usr/bin/env bash
# v0.1.9 read-only host diagnosis: does not change networks, VMs, DB or secrets.
set -uo pipefail
FAILURES=0
PASS=0
SKIP=0

ok() { printf '[OK] %s\n' "$*"; PASS=$((PASS+1)); }
fail() { printf '[FAIL] %s\n' "$*" >&2; FAILURES=$((FAILURES+1)); }
skip() { printf '[INFO] %s\n' "$*"; SKIP=$((SKIP+1)); }
unit() {
    local service="$1"
    if systemctl is-active --quiet "$service"; then ok "$service 실행 중"
    else fail "$service 미실행: systemctl status $service --no-pager"; fi
}

echo 'Personal KVM v0.1.9 호스트 진단 (읽기 전용)'
if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    case "${ID:-}:${VERSION_ID:-}" in rocky:9.*|rocky:10.*) ok "운영체제 ${PRETTY_NAME}";;
       *) fail "미검증 운영체제 ${PRETTY_NAME:-unknown}";; esac
else fail '/etc/os-release 읽기 실패'; fi

if [ -c /dev/kvm ]; then ok '/dev/kvm 장치 있음'; else fail '/dev/kvm 장치 없음'; fi
for service in pkvm-backend.service pkvm-lb-agent.service nginx.service; do unit "$service"; done
if [ -S /run/pkvm-lb-agent/control.sock ]; then ok 'LB Agent UNIX 소켓 존재'
else fail 'LB Agent UNIX 소켓 없음'; fi

if command -v virsh >/dev/null 2>&1 && virsh -c qemu:///system uri >/dev/null 2>&1; then
    ok 'libvirt 연결 성공'
else fail 'libvirt 연결 실패'; fi
if command -v nginx >/dev/null 2>&1 && nginx -t >/dev/null 2>&1; then ok 'nginx 구문 검사 통과'
else fail 'nginx 구문 검사 실패'; fi
if curl --silent --show-error --fail --max-time 5 http://127.0.0.1:8000/api/health/ready >/dev/null 2>&1; then
    ok '백엔드 준비 API 응답 정상'
else fail '백엔드 준비 API 응답 실패: curl http://127.0.0.1:8000/api/health/ready'; fi

if [ -f /etc/haproxy/pkvm-managed.cfg ]; then
    if command -v haproxy >/dev/null 2>&1 && haproxy -c -f /etc/haproxy/pkvm-managed.cfg >/dev/null 2>&1; then
        ok 'HAProxy 관리 설정 구문 검사 통과'
    else fail 'HAProxy 관리 설정 구문 검사 실패'; fi
    unit pkvm-haproxy.service
    unit pkvm-ha-monitor.service
else skip '관리형 HAProxy 설정 없음 (L4 미설치라면 정상)'; fi

if command -v traefik >/dev/null 2>&1; then
    if traefik version 2>/dev/null | awk '/^Version:/ {split($2,v,"."); sub(/^v/,"",v[1]); if (v[1]>3 || v[1]==3 && v[2]>=4) good=1} END {exit !good}'; then
        ok 'Traefik 버전 >= 3.4 확인'
    else fail 'Traefik 3.4 이상 필요 또는 버전 확인 실패'; fi
    unit pkvm-traefik.service
else skip 'Traefik 미설치 (L7 미사용이라면 정상)'; fi

if [ -f /var/lib/personal-kvm/install-state/failed-stage ]; then
    skip "마지막 설치 실패 단계: $(head -n 1 /var/lib/personal-kvm/install-state/failed-stage)"
fi
if [ -x /opt/personal-kvm/backend/venv/bin/python ]; then
    if /opt/personal-kvm/backend/venv/bin/python -B - <<'PY'
import ast
from pathlib import Path
for path in Path('/opt/personal-kvm/backend/app').rglob('*.py'):
    ast.parse(path.read_text(encoding='utf-8'),filename=str(path))
PY
    then
        ok '백엔드 Python 코드 구문 검사 통과'
    else fail '백엔드 Python 코드 구문 검사 실패'; fi
else fail '백엔드 Python 가상환경 없음'; fi

echo "검사 결과: 통과 ${PASS}건 / 오류 ${FAILURES}건 / 안내 ${SKIP}건"
echo '오류 상세: sudo systemctl status <서비스명> --no-pager'
echo '서비스 로그: sudo journalctl -u <서비스명> -n 100 --no-pager'
echo '주의: 로그 및 인증정보를 외부 공유하기 전에 비밀번호·토큰·IP·호스트명을 가리세요.'
test "$FAILURES" -eq 0
