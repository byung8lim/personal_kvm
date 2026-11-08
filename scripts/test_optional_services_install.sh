#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bash -n "$ROOT/scripts/install.sh"
bash -n "$ROOT/scripts/setup_lb.sh"
grep -Fq "dns.get('enabled')" "$ROOT/scripts/install.sh"
grep -Fq 'setup_nfs.sh' "$ROOT/scripts/install.sh"
grep -Fq 'setup_lb.sh haproxy' "$ROOT/scripts/install.sh"
grep -Fq 'setup_lb.sh traefik' "$ROOT/scripts/install.sh"
grep -Fq 'api.github.com/repos/traefik/traefik/releases/tags' "$ROOT/scripts/setup_lb.sh"
grep -Fq 'Accept: application/octet-stream' "$ROOT/scripts/setup_lb.sh"
grep -Fq 'a564abd5fc8ee97d838270f188bb94cb3172cc113f4b51aa5e7dd6117eedb393' "$ROOT/scripts/setup_lb.sh"
python3 -m py_compile "$ROOT/backend/app/main.py"
printf '[PASS] DNS/NFS/LB install integration checks\n'
