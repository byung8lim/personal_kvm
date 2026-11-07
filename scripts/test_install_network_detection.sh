#!/usr/bin/env bash
# Regression checks for installer reruns after the host IP moves to a bridge.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALLER="${ROOT}/scripts/install.sh"

bash -n "${INSTALLER}"
grep -Fq 'DEFAULT_ROUTE_IFACE=' "${INSTALLER}"
grep -Fq 'detect_physical_nic()' "${INSTALLER}"
grep -Fq '/brif/' "${INSTALLER}"
grep -Fq 'DEFAULT_ROUTE_IFACE:-${PHYS_NIC}' "${INSTALLER}"

printf '[PASS] Installer bridge rerun detection regression checks\n'
