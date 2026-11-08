#!/usr/bin/env bash
# Explicit opt-in LB service bootstrap, no secret or public listener auto-generation.
set -euo pipefail
die(){ echo "[LB 오류] $*" >&2; exit 1; }
install_traefik_binary() {
  local version="3.7.17"
  local archive="traefik_v${version}_linux_amd64.tar.gz"
  local checksum="a564abd5fc8ee97d838270f188bb94cb3172cc113f4b51aa5e7dd6117eedb393"
  local work
  [[ "$(uname -m)" == "x86_64" ]] || die 'Automatic Traefik install currently supports x86_64 only'
  work="$(mktemp -d /tmp/pkvm-traefik.XXXXXXXX)"
  trap 'rm -rf "$work"' RETURN
  if ! curl --fail --location --retry 5 --retry-all-errors --connect-timeout 15 \
      --output "$work/$archive" \
      "https://github.com/traefik/traefik/releases/download/v${version}/${archive}"; then
    # Some GitHub regional release-download edges return 504 while the Releases
    # API and its object-storage redirect remain healthy. Resolve the immutable
    # asset API URL by exact filename, then apply the same pinned checksum.
    local asset_url
    asset_url="$(curl --fail --silent --show-error --retry 5 --retry-all-errors \
      "https://api.github.com/repos/traefik/traefik/releases/tags/v${version}" | \
      python3 -c 'import json,sys; name=sys.argv[1]; data=json.load(sys.stdin); print(next(x["url"] for x in data["assets"] if x["name"]==name))' "$archive")"
    [[ "$asset_url" == https://api.github.com/repos/traefik/traefik/releases/assets/* ]] || \
      die 'Unexpected Traefik release asset URL'
    curl --fail --location --retry 5 --retry-all-errors --connect-timeout 15 \
      -H 'Accept: application/octet-stream' --output "$work/$archive" "$asset_url"
  fi
  printf '%s  %s\n' "$checksum" "$work/$archive" | sha256sum -c -
  tar -xzf "$work/$archive" -C "$work" traefik
  install -o root -g root -m 0755 "$work/traefik" /usr/local/bin/traefik
  rm -rf "$work"
  trap - RETURN
}
[[ $EUID -eq 0 && $# -eq 2 ]] || die 'Usage: sudo setup_lb.sh <traefik|haproxy> <port>'
SERVICE="$1"; PORT="$2"
[[ "$PORT" =~ ^[0-9]{1,5}$ ]] && (( PORT>=1 && PORT<=65534 )) || die 'Invalid port'
case "$SERVICE" in
haproxy)
  rpm -q haproxy >/dev/null 2>&1 || dnf install -y haproxy
  command -v haproxy >/dev/null || die 'HAProxy binary missing'
  [[ -r /etc/haproxy/haproxy.cfg ]] || die 'HAProxy config missing'
  haproxy -c -f /etc/haproxy/haproxy.cfg || die 'Existing HAProxy configuration invalid'
  # Keep the managed engine healthy before the first L4 service is published.
  # The baseline has no public listener; apply_haproxy_services.py atomically
  # replaces it when an operator creates a validated L4 definition.
  if [[ ! -f /etc/haproxy/pkvm-managed.cfg ]]; then
    cat >/etc/haproxy/pkvm-managed.cfg <<'HAPROXY'
global
    log stdout format raw local0
    maxconn 20000
    stats socket /run/pkvm-haproxy/admin.sock mode 600 level admin
defaults
    log global
    mode tcp
    option tcplog
    timeout connect 5s
    timeout client 60s
    timeout server 60s
HAPROXY
    chmod 0644 /etc/haproxy/pkvm-managed.cfg
  fi
  haproxy -c -f /etc/haproxy/pkvm-managed.cfg || die 'Managed HAProxy configuration invalid'
  install -d -o root -g root -m 0750 /etc/personal-kvm
  [[ -f /etc/personal-kvm/ha-services.json ]] || printf '[]\n' >/etc/personal-kvm/ha-services.json
  chmod 0600 /etc/personal-kvm/ha-services.json
  cat >/etc/systemd/system/pkvm-haproxy.service <<UNIT
[Unit]
Description=Personal KVM managed HAProxy TCP service
After=network-online.target
Wants=network-online.target
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
  systemctl enable --now pkvm-haproxy.service
  systemctl enable --now pkvm-ha-monitor.service
  systemctl is-active --quiet pkvm-haproxy.service || die 'HAProxy did not start'
  systemctl is-active --quiet pkvm-ha-monitor.service || die 'HAProxy monitor did not start'
  ;;
traefik)
  if [[ -x /usr/local/bin/traefik ]]; then
    TRAEFIK_BIN="/usr/local/bin/traefik"
  else
    TRAEFIK_BIN="$(command -v traefik || true)"
  fi
  if [[ -z "$TRAEFIK_BIN" ]]; then
    install_traefik_binary
    # Non-interactive sudo may omit /usr/local/bin from its secure PATH.
    # Use the exact verified installation target instead of relying on PATH.
    TRAEFIK_BIN="/usr/local/bin/traefik"
  fi
  [[ -x "$TRAEFIK_BIN" ]] || die 'Verified Traefik binary is not executable'
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
