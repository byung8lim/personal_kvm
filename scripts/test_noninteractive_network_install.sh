#!/usr/bin/env bash
# Regression checks for explicit non-interactive bridge migration opt-in.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NETWORK_SETUP="${ROOT}/scripts/setup_networks.sh"

bash -n "${NETWORK_SETUP}"
grep -Fq 'PKVM_ALLOW_NONINTERACTIVE_NETWORK:-0' "${NETWORK_SETUP}"
grep -Fq "printf 'Yes\\n'" "${NETWORK_SETUP}"
grep -Fq 'CHECKPOINT_COMMAND=' "${NETWORK_SETUP}"
grep -Fq 'checkpoint_failed=1' "${NETWORK_SETUP}"
grep -Fq 'virsh net-list --name' "${NETWORK_SETUP}"
grep -Fq 'END { exit !found }' "${NETWORK_SETUP}"

printf '[PASS] Non-interactive network installer regression checks\n'
