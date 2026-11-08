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
exec bash "${ROOT}/scripts/install.sh"
