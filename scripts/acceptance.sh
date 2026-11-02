#!/bin/bash
set -euo pipefail

MODE="${1:-}"
EVIDENCE_DIR="${PKVM_EVIDENCE_DIR:-/var/tmp/pkvm-acceptance-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "${EVIDENCE_DIR}"
exec > >(tee -a "${EVIDENCE_DIR}/${MODE:-usage}.log") 2>&1

record() { echo "[$(date --iso-8601=seconds)] $*"; "$@"; }
require_isolated() {
    [ "${PKVM_ACCEPT_DESTRUCTIVE:-}" = "YES" ] || {
        echo "격리 호스트에서 PKVM_ACCEPT_DESTRUCTIVE=YES를 설정해야 합니다." >&2
        exit 2
    }
}

case "${MODE}" in
preflight)
    record cat /etc/os-release
    record lscpu
    record df -h
    record rpm -qa
    record ip -details address
    record ip route
    record nmcli connection show
    record virsh -c qemu:///system list --all
    record firewall-cmd --list-all || true
    ;;
postinstall|postreboot)
    require_isolated
    record systemctl is-active pkvm-backend nginx mysqld \
        virtqemud.socket virtnetworkd.socket virtstoraged.socket virtproxyd.socket
    record systemctl show pkvm-backend -p User -p Group -p NoNewPrivileges \
        -p ProtectSystem -p ProtectKernelModules
    record ss -lntp
    record curl --fail --silent --show-error --insecure https://127.0.0.1/api/health/ready
    ! curl --fail --silent --connect-timeout 2 "http://$(hostname -I | awk '{print $1}'):8000/api/health"
    record openssl x509 -in /etc/personal-kvm/tls/server.crt -noout -subject -issuer -dates
    record sudo -u pkvm virsh -c qemu:///system uri
    pid="$(systemctl show pkvm-backend -p MainPID --value)"
    record nsenter -t "${pid}" -m runuser -u pkvm -- \
        env HOME=/var/lib/personal-kvm LIBGUESTFS_BACKEND=direct libguestfs-test-tool
    record test -s /data/images/Rocky-9-GenericCloud-Base.latest.x86_64.qcow2
    record /opt/personal-kvm/backend/venv/bin/alembic \
        -c /opt/personal-kvm/backend/alembic.ini current
    PKVM_RUN_LIVE_INTEGRATION=1 PYTHONPATH=/opt/personal-kvm/backend \
        record /opt/personal-kvm/backend/venv/bin/python -m unittest discover \
        -s /opt/personal-kvm/backend/tests/integration -v
    ;;
preuninstall)
    require_isolated
    record virsh -c qemu:///system list --all
    record mysql -u root -e \
        "SELECT id,name,state,last_desired_state FROM personal_kvm.instances"
    ;;
postuninstall)
    require_isolated
    ! systemctl is-active --quiet pkvm-backend
    ! test -e /etc/systemd/system/pkvm-backend.service
    record ip -details address
    record ip route
    record nmcli connection show
    record rpm -qa
    ;;
*)
    echo "사용법: $0 {preflight|postinstall|postreboot|preuninstall|postuninstall}" >&2
    exit 2
    ;;
esac

record sha256sum "${EVIDENCE_DIR}/${MODE}.log"
