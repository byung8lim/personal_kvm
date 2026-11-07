#!/usr/bin/env bash
# compute.yaml is validated without changing host state before invoking install.sh.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SPEC="${1:-${ROOT}/compute.yaml}"
[[ $EUID -eq 0 ]] || { echo '[오류] root 권한 필요' >&2; exit 1; }
[[ -r "$SPEC" ]] || { echo "[오류] compute.yaml 읽기 실패: $SPEC" >&2; exit 1; }
ENV_EXPORTS="$(python3 "${ROOT}/scripts/validate_compute.py" "$SPEC")" || exit $?
# All values are emitted through shlex.quote by validated trusted Python code.
eval "$ENV_EXPORTS"
export PKVM_COMPUTE_CONFIG="$SPEC"
# Do not silently imply that unsupported add-ons are installed.
python3 - "$SPEC" <<'PY'
import sys,yaml
with open(sys.argv[1], encoding='utf-8') as stream: cfg=yaml.safe_load(stream)
unsupported=[key for key in ("nfs","traefik","haproxy") if cfg["services"][key]]
if unsupported:
    sys.exit("[오류] 설치기가 아직 자동 구성하지 않는 서비스: "+", ".join(unsupported))
PY
exec bash "${ROOT}/scripts/install.sh"
