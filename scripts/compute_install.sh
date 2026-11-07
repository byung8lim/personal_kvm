#!/usr/bin/env bash
# Fully validate compute.yaml before changing installed services.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SPEC="${1:-${ROOT}/compute.yaml}"
[[ $EUID -eq 0 ]] || { echo '[오류] root 권한이 필요합니다.' >&2; exit 1; }
[[ -r "$SPEC" ]] || { echo "[오류] compute.yaml을 읽을 수 없습니다: $SPEC" >&2; exit 1; }
export PKVM_COMPUTE_CONFIG="$SPEC"
ENV_EXPORTS="$(python3 "${ROOT}/scripts/validate_compute.py" "$SPEC")" || exit $?
eval "$ENV_EXPORTS"
# Traefik requires a verified binary/package source; do not silently claim it installed.
python3 - "$SPEC" <<'PY'
import sys,yaml
with open(sys.argv[1], encoding='utf-8') as f: data=yaml.safe_load(f)
if data['services']['traefik']:
    import shutil
    if not shutil.which('traefik'):
        sys.exit('[오류] Traefik 설치 파일이 없습니다. 검증된 실행 파일을 먼저 배포해야 합니다.')
PY
bash "${ROOT}/scripts/install.sh"
if [[ "${PKVM_SETUP_BIND9:-0}" == 1 ]]; then
  python3 "${ROOT}/scripts/setup_split_dns.py" \
    --private "$PKVM_DNS_FORWARD_ZONE" --external "$PKVM_EXTERNAL_DOMAIN" \
    --reverse "$PKVM_DNS_REVERSE_ZONE" --server "$PKVM_DNS_SERVER_IP" \
    --internal "$PKVM_INTERNAL_CIDR" --management "$PKVM_MGMT_CIDR" --apply
fi
# Optional components depend on the base installation's pkvm account and network.
if [[ "${PKVM_SETUP_NFS:-0}" == 1 ]]; then
  bash "${ROOT}/scripts/setup_nfs.sh" "$PKVM_NFS_DIR" "$PKVM_MGMT_CIDR"
  # persist the NFS root in the same config loaded by the service
  python3 - /etc/personal-kvm/config.yaml "$PKVM_NFS_DIR" <<'PY'
import os,sys,tempfile,yaml,grp
from pathlib import Path
p=Path(sys.argv[1])
with p.open(encoding='utf-8') as f: config=yaml.safe_load(f)
config['nfs']={'NFS_DIR':sys.argv[2]}
fd,tmp=tempfile.mkstemp(prefix='.pkvm-config-',dir=str(p.parent),text=True)
try:
  with os.fdopen(fd,'w',encoding='utf-8') as f:
    yaml.safe_dump(config,f,allow_unicode=True,sort_keys=False)
    f.flush();os.fsync(f.fileno())
  os.chown(tmp,0,grp.getgrnam('pkvm').gr_gid);os.chmod(tmp,0o640)
  os.replace(tmp,p)
finally:
  if os.path.exists(tmp): os.unlink(tmp)
PY
fi
if [[ "${PKVM_SETUP_HAPROXY:-0}" == 1 ]]; then
  bash "${ROOT}/scripts/setup_lb.sh" haproxy "$PKVM_HAPROXY_PORT"
fi
if [[ "${PKVM_SETUP_TRAEFIK:-0}" == 1 ]]; then
  bash "${ROOT}/scripts/setup_lb.sh" traefik "$PKVM_TRAEFIK_PORT"
fi
if [[ "${PKVM_SETUP_HAPROXY:-0}" == 1 || "${PKVM_SETUP_TRAEFIK:-0}" == 1 ]]; then
  install -D -m 0644 /etc/personal-kvm/systemd/pkvm-lb-agent.service /etc/systemd/system/pkvm-lb-agent.service
  systemctl daemon-reload
  systemctl enable --now pkvm-lb-agent.service
fi
systemctl restart pkvm-backend.service
echo '[완료] compute.yaml 기반 설치 및 선택 서비스 설정이 끝났습니다.'
