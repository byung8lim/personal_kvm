#!/usr/bin/env bash
# Regression checks for optional Bind9 install integration. Does not change host state.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bash -n "$ROOT/scripts/install.sh"
bash -n "$ROOT/scripts/setup_bind9.sh"
grep -Fq 'PKVM_SETUP_BIND9:-0' "$ROOT/scripts/install.sh"
grep -Fq 'bash /opt/personal-kvm/scripts/setup_bind9.sh' "$ROOT/scripts/install.sh"
grep -Fq "CURRENT_STAGE=\"bind9\"" "$ROOT/scripts/install.sh"
grep -Fq 'named-checkconf -z' "$ROOT/scripts/setup_bind9.sh"
grep -Fq 'update-policy' "$ROOT/scripts/setup_bind9.sh"
printf '[PASS] Bind9 installer syntax and integration checks\n'
