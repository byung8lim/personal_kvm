#!/usr/bin/env bash
set -euo pipefail
[[ $EUID -eq 0 && $# -eq 2 ]] || { echo 'Usage: sudo setup_lb.sh <traefik|haproxy> <port>' >&2; exit 2; }
SERVICE="$1"; PORT="$2"
[[ "$PORT" =~ ^[0-9]{1,5}$ ]] && (( PORT>=1 && PORT<=65535 )) || exit 2
case "$SERVICE" in
  haproxy)
    rpm -q haproxy >/dev/null 2>&1 || dnf install -y haproxy
    haproxy -c -f /etc/haproxy/haproxy.cfg
    systemctl enable --now haproxy
    ;;
  traefik)
    command -v traefik >/dev/null 2>&1 || { echo 'Traefik binary not available in configured repositories. Supply a verified package.' >&2; exit 2; }
    traefik version
    ;;
  *) echo 'Unknown LB engine' >&2; exit 2;;
esac
