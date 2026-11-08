#!/usr/bin/env bash
# Regression checks for asynchronous systemd socket readiness.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALLER="${ROOT}/scripts/install.sh"

bash -n "${INSTALLER}"
grep -Fq 'for _ in $(seq 1 40)' "${INSTALLER}"
grep -Fq '[ -S /run/pkvm-lb-agent/control.sock ]' "${INSTALLER}"
grep -Fq 'sleep 0.25' "${INSTALLER}"

printf '[PASS] Service socket readiness regression checks\n'
