#!/usr/bin/env bash
# Regression checks for Rocky 9 Node.js stream selection. Does not change host state.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALLER="${ROOT}/scripts/install.sh"

bash -n "${INSTALLER}"
grep -Fq 'dnf -y module reset nodejs' "${INSTALLER}"
grep -Fq 'dnf -y module enable nodejs:22' "${INSTALLER}"
grep -Fq 'dnf -y install --allowerasing nodejs npm' "${INSTALLER}"
grep -Fq 'ensure_supported_nodejs' "${INSTALLER}"
grep -Fq 'node_version_is_supported' "${INSTALLER}"

printf '[PASS] Rocky 9 Node.js installer regression checks\n'
