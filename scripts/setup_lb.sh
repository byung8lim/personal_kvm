#!/usr/bin/env bash
# Explicit opt-in LB service bootstrap, no secret or public listener auto-generation.
set -euo pipefail
die(){ echo "[LB 오류] $*" >&2; exit 1; }
[[ $EUID -eq 0 && $# -eq 2 ]] || die 'Usage: sudo setup_lb.sh <traefik|haproxy> <port>'
SERVICE="$1"; PORT="$2"
[[ "$PORT" =~ ^[0-9]{1,5}$ ]] && (( PORT>=1 && PORT<=65534 )) || die 'Invalid port'
case "$SERVICE" in
haproxy)
  rpm -q haproxy >/dev/null 2>&1 || dnf install -y haproxy
  command -v haproxy >/dev/null || die 'HAProxy binary missing'
  [[ -r /etc/haproxy/haproxy.cfg ]] || die 'HAProxy config missing'
  haproxy -c -f /etc/haproxy/haproxy.cfg || die 'Existing HAProxy configuration invalid'
  # Managed service starts only after an operator publishes a validated L4 definition.
  cat >/etc/systemd/system/pkvm-haproxy.service <<UNIT
[Unit]
Description=Personal KVM managed HAProxy TCP service
After=network-online.target
Wants=network-online.target
ConditionPathExists=/etc/haproxy/pkvm-managed.cfg
[Service]
Type=notify
RuntimeDirectory=pkvm-haproxy
RuntimeDirectoryMode=0750
ExecStart=/usr/sbin/haproxy -Ws -f /etc/haproxy/pkvm-managed.cfg -p /run/pkvm-haproxy.pid
ExecReload=/bin/kill -USR2 \$MAINPID
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload
  cat >/etc/systemd/system/pkvm-ha-monitor.service <<UNIT
[Unit]
Description=Personal KVM HAProxy backend failover monitor
After=pkvm-haproxy.service
Requires=pkvm-haproxy.service
[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/personal-kvm/scripts/ha_manager.py --monitor
Restart=always
RestartSec=5
NoNewPrivileges=true
ProtectHome=true
[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload
  systemctl enable pkvm-haproxy.service
  systemctl enable pkvm-ha-monitor.service
  ;;
traefik)
  TRAEFIK_BIN="$(command -v traefik || true)"
  [[ -n "$TRAEFIK_BIN" ]] || die 'Verified Traefik binary must be installed in PATH'
  # All 4 requested strategies are available in Traefik >= 3.4.
  TRAEFIK_VERSION="$($TRAEFIK_BIN version | awk '/^Version:/ {print $2; exit}')"
  if ! python3 - "$TRAEFIK_VERSION" <<'PY'
import re,sys
v=re.match(r'^v?(\d+)\.(\d+)(?:\.\d+)?',sys.argv[1])
sys.exit(0 if v and (int(v[1]),int(v[2])) >= (3,4) else 1)
PY
  then
    die 'Traefik >= 3.4 필수: 설치된 버전 확인 실패 또는 지원 버전 미달'
  fi
  # Treat port as HTTPS (websecure); HTTP binds to adjacent high port.
  HTTP_PORT=$((PORT+1))
  id -u pkvm-traefik >/dev/null 2>&1 || useradd -r -s /sbin/nologin -d /var/lib/pkvm-traefik pkvm-traefik
  install -d -o root -g pkvm-traefik -m 0750 /etc/personal-kvm/traefik
  install -d -o root -g pkvm-traefik -m 0750 /etc/personal-kvm/traefik/dynamic
  cat >/etc/personal-kvm/traefik/traefik.yaml <<YAML
entryPoints:
  web:
    address: ":$HTTP_PORT"
  websecure:
    address: ":$PORT"
providers:
  file:
    directory: /etc/personal-kvm/traefik/dynamic
    watch: true
api:
  dashboard: false
  insecure: false
log:
  level: INFO
YAML
  chown root:pkvm-traefik /etc/personal-kvm/traefik/traefik.yaml
  chmod 0640 /etc/personal-kvm/traefik/traefik.yaml
  cat >/etc/systemd/system/pkvm-traefik.service <<UNIT
[Unit]
Description=Personal KVM Traefik L7 proxy
After=network-online.target
Wants=network-online.target
[Service]
User=pkvm-traefik
Group=pkvm-traefik
ExecStart=$TRAEFIK_BIN --configFile=/etc/personal-kvm/traefik/traefik.yaml
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload
  systemctl enable --now pkvm-traefik.service
  systemctl is-active --quiet pkvm-traefik.service || die 'Traefik did not start'
  ;;
*) die 'Unknown LB engine';;
esac
