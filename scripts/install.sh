#!/bin/bash
# scripts/install.sh : 퍼스널 KVM 가상화 서비스 v0.0.7 원클릭 자동 설치 및 구성 스크립트
set -euo pipefail

if [ "${EUID}" -ne 0 ]; then
    echo "[오류] install.sh는 반드시 root 권한(sudo)으로 실행해야 합니다."
    exit 1
fi

if [ ! -r /etc/os-release ]; then
    echo "[오류] /etc/os-release를 읽을 수 없습니다." >&2; exit 1
fi
# shellcheck disable=SC1091
. /etc/os-release
ROCKY_MAJOR="${VERSION_ID%%.*}"
if [ "${ID:-}" != "rocky" ] || { [ "${VERSION_ID}" != "9.8" ] && [ "${ROCKY_MAJOR}" != "10" ]; }; then
    echo "[오류] Rocky Linux 9.8 또는 10.x만 지원합니다. 현재: ${PRETTY_NAME:-unknown}" >&2; exit 1
fi
if ! grep -Eq '(vmx|svm)' /proc/cpuinfo; then
    echo "[오류] CPU 가상화 확장(Intel VT-x/AMD-V)을 사용할 수 없습니다." >&2; exit 1
fi
if [ "$(df -Pk / | awk 'NR==2 {print $4}')" -lt 5242880 ]; then
    echo "[오류] 루트 파일시스템에 최소 5GB의 여유 공간이 필요합니다." >&2; exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "${SCRIPT_DIR}")"

require_project_file() {
    local relative_path="$1"
    if [ ! -f "${PROJECT_ROOT}/${relative_path}" ]; then
        echo "[오류] 설치에 필요한 파일이 없습니다: ${PROJECT_ROOT}/${relative_path}" >&2
        exit 1
    fi
}

# 호스트 설정을 변경하기 전에 배포 패키지의 필수 자산을 먼저 검증한다.
require_project_file "backend/requirements.txt"
require_project_file "backend/app/main.py"
require_project_file "frontend/package.json"
require_project_file "frontend/package-lock.json"
require_project_file "frontend/index.html"
require_project_file "config/systemd/pkvm-backend.service"
require_project_file "config/systemd/pkvm-tls-renew.service"
require_project_file "config/systemd/pkvm-tls-renew.timer"
require_project_file "config/nginx/personal-kvm.conf"
require_project_file "database/schema.sql"
require_project_file "templates/grub_default.tpl"
require_project_file "scripts/customize_guest.sh"
require_project_file "scripts/setup_networks.sh"
require_project_file "scripts/ensure_networks.sh"
require_project_file "scripts/renew_tls.sh"
require_project_file "config/config.yaml"

ADMIN_USER="${PKVM_ADMIN_USER:-admin}"
ADMIN_PASS="${PKVM_ADMIN_PASS:-}"
PHYS_NIC="${PKVM_PHYS_NIC:-$(ip route | awk '/default/ {print $5; exit}')}"
PHYS_NIC="${PHYS_NIC:-enp3s0}"
HOST_BR_IP="${PKVM_HOST_BR_IP:-$(ip -4 -o addr show dev "${PHYS_NIC}" 2>/dev/null | awk '{print $4; exit}')}"
HOST_BR_IP="${HOST_BR_IP:-192.168.0.6/24}"
HOST_BR_GW="${PKVM_HOST_BR_GW:-$(ip route | awk '/default/ {print $3; exit}')}"
HOST_BR_GW="${HOST_BR_GW:-192.168.0.1}"

echo "=================================================================="
echo " 퍼스널 KVM 가상화 서비스 v0.0.7 설치 시작 (Rocky Linux 9.8 / 10.x)"
echo "=================================================================="

INSTALL_STATE_DIR="/var/lib/personal-kvm/install-state"
mkdir -p "${INSTALL_STATE_DIR}"
if [ ! -f "${INSTALL_STATE_DIR}/captured" ]; then
    cp -a /etc/nginx/nginx.conf "${INSTALL_STATE_DIR}/nginx.conf.before" 2>/dev/null || true
    cp -a /etc/NetworkManager/system-connections "${INSTALL_STATE_DIR}/nm-connections.before" 2>/dev/null || true
    rpm -qa | sort > "${INSTALL_STATE_DIR}/packages.before"
    touch "${INSTALL_STATE_DIR}/captured"
    chmod -R go-rwx "${INSTALL_STATE_DIR}"
fi

CURRENT_STAGE="preflight"
INSTALL_COMPLETED=0
on_install_exit() {
    local exit_code=$?
    if [ "${INSTALL_COMPLETED}" -eq 0 ] && [ "${exit_code}" -ne 0 ]; then
        echo "[오류] 설치 단계 '${CURRENT_STAGE}'에서 실패했습니다. 재진단을 위해 실패 단계를 기록합니다." >&2
        printf '%s\n' "${CURRENT_STAGE}" > "${INSTALL_STATE_DIR}/failed-stage"
        echo "[진단] 실패 단계: ${CURRENT_STAGE}" >&2
        systemctl status pkvm-backend.service nginx --no-pager >&2 || true
        journalctl -u pkvm-backend.service -u nginx -n 100 --no-pager >&2 || true
        if [ -s /etc/personal-kvm/.initial_admin_password ]; then
            echo "[안내] 초기 관리자: ${ADMIN_USER} / $(tr -d '\r\n' < /etc/personal-kvm/.initial_admin_password)" >&2
            echo "[안내] 암호 재확인: sudo cat /etc/personal-kvm/.initial_admin_password" >&2
        fi
    fi
}
trap on_install_exit EXIT

CURRENT_STAGE="packages"
echo "[1/7] 필수 패키지 점검 및 설치..."
dnf install -y epel-release || true
dnf config-manager --set-enabled crb 2>/dev/null || true
dnf install -y qemu-kvm libvirt virt-install virt-top \
    libguestfs-tools guestfs-tools cloud-utils-growpart \
    mysql-server mysql python3 python3-pip python3-devel python3-pyyaml gcc \
    nginx curl wget tar unzip jq genisoimage policycoreutils-python-utils firewalld openssl
if ! command -v genisoimage >/dev/null 2>&1 && ! command -v cloud-localds >/dev/null 2>&1; then
    echo "[오류] cloud-init seed 생성 도구가 없습니다. EPEL의 genisoimage 설치 상태를 확인하십시오." >&2
    exit 1
fi
for required_command in virsh qemu-img virt-customize libguestfs-test-tool supermin; do
    if ! command -v "${required_command}" >/dev/null 2>&1; then
        echo "[오류] 필수 가상화 도구를 찾지 못했습니다: ${required_command}" >&2
        exit 1
    fi
done
rpm -qa | sort > "${INSTALL_STATE_DIR}/packages.after"
comm -13 "${INSTALL_STATE_DIR}/packages.before" "${INSTALL_STATE_DIR}/packages.after" > "${INSTALL_STATE_DIR}/packages.added"

if ! command -v python3.11 >/dev/null 2>&1; then
    dnf install -y python3.11 python3.11-pip python3.11-devel || true
fi
PYTHON_BIN="python3.11"
if ! command -v python3.11 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
fi
find_qemu_emulator() {
    local candidate

    # qemu-kvm is installed outside PATH on Rocky/RHEL (normally /usr/libexec).
    for candidate in \
        "$(command -v qemu-kvm 2>/dev/null || true)" \
        "$(command -v qemu-system-x86_64 2>/dev/null || true)" \
        /usr/libexec/qemu-kvm \
        /usr/bin/qemu-kvm \
        /usr/bin/qemu-system-x86_64; do
        if [ -n "${candidate}" ] && [ -f "${candidate}" ] && [ -x "${candidate}" ]; then
            printf '%s\n' "${candidate}"
            return 0
        fi
    done

    return 1
}

QEMU_EMULATOR_PATH="${QEMU_EMULATOR_PATH:-$(find_qemu_emulator || true)}"
if [ -z "${QEMU_EMULATOR_PATH}" ] || [ ! -x "${QEMU_EMULATOR_PATH}" ]; then
    echo "[오류] 실행 가능한 QEMU emulator를 찾지 못했습니다." >&2
    exit 1
fi

# Always build UI from source.  A bundled dist may have been produced before
# the current API contract changed (for example, before guest_password became
# required), so treating its mere presence as current can deploy a broken UI.
# This must occur before copying the frontend to /opt/personal-kvm.
CURRENT_STAGE="frontend-build"
echo "[frontend] 현재 소스에서 프런트엔드를 빌드합니다."
if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
    dnf module reset -y nodejs >/dev/null 2>&1 || true
    dnf module enable -y nodejs:22 >/dev/null 2>&1 || true
    dnf install -y nodejs npm
fi
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
if [ "${NODE_MAJOR}" -lt 20 ]; then
    echo "[오류] Node.js 20.19+ 또는 22.12+ 필요 (현재: $(node --version)). Node.js 22 설치 후 다시 실행하십시오." >&2
    exit 1
fi
(cd "${PROJECT_ROOT}/frontend" && npm ci --no-audit --no-fund && npm run build)
if [ ! -s "${PROJECT_ROOT}/frontend/dist/index.html" ]; then
    echo "[오류] 프런트엔드 빌드 결과가 없습니다: frontend/dist/index.html" >&2
    exit 1
fi

CURRENT_STAGE="base-services"
echo "[2/7] libvirtd 및 mysqld 데몬 활성화..."
systemctl enable --now libvirtd
systemctl enable --now virtqemud.socket virtnetworkd.socket virtstoraged.socket virtproxyd.socket
systemctl enable --now mysqld

if ! getent group pkvm >/dev/null; then
    groupadd --system pkvm
fi
if ! id -u pkvm >/dev/null 2>&1; then
    useradd --system --gid pkvm --home-dir /var/lib/personal-kvm --create-home --shell /sbin/nologin pkvm
fi
# INSTALL_STATE_DIR creates this path as root before the service account exists.
# Keep the root-owned install-state contents intact, but make the actual home
# directory writable by pkvm for libvirt/libguestfs caches.
chown pkvm:pkvm /var/lib/personal-kvm
for group in kvm libvirt; do
    getent group "${group}" >/dev/null && usermod -a -G "${group}" pkvm
done

PKVM_SSH_DIR="/var/lib/personal-kvm/.ssh"
if [ ! -f "${PKVM_SSH_DIR}/id_rsa" ]; then
    install -d -o pkvm -g pkvm -m 0700 "${PKVM_SSH_DIR}"
    runuser -u pkvm -- ssh-keygen -t rsa -b 4096 -N "" -f "${PKVM_SSH_DIR}/id_rsa"
    touch "${INSTALL_STATE_DIR}/ssh-key-created"
fi

CURRENT_STAGE="configuration"
echo "[3/7] 스토리지 풀 디렉터리 및 /etc/personal-kvm/.env 환경설정 배포..."
mkdir -p /data/images /data/instances /data2/disks
chmod 0755 /data /data2
chown -R pkvm:kvm /data/images /data/instances /data2/disks
chmod 0770 /data/images /data/instances /data2/disks

mkdir -p /etc/personal-kvm /opt/personal-kvm

if [ -d "${PROJECT_ROOT}/config" ]; then
    cp -rf "${PROJECT_ROOT}/config/." /etc/personal-kvm/
fi

# Persist the host values detected before installation.  Keeping the sample
# NIC/address in config.yaml made later repair and diagnostics target the wrong
# interface even though the first install invocation used the detected values.
python3 - /etc/personal-kvm/config.yaml "${PHYS_NIC}" "${HOST_BR_IP}" "${HOST_BR_GW}" <<'PY'
import sys
import yaml

path, physical_nic, host_ip_cidr, gateway = sys.argv[1:]
with open(path, encoding="utf-8") as handle:
    config = yaml.safe_load(handle)
bridged = config["networks"]["bridged"]
bridged["physical_nic"] = physical_nic
bridged["host_ip_cidr"] = host_ip_cidr
bridged["gateway"] = gateway
with open(path, "w", encoding="utf-8") as handle:
    yaml.safe_dump(config, handle, allow_unicode=True, sort_keys=False)
PY

if [ -f "${PROJECT_ROOT}/config/.env" ]; then
    cp -f "${PROJECT_ROOT}/config/.env" /etc/personal-kvm/.env
elif [ -f "${PROJECT_ROOT}/config/env.default" ]; then
    cp -f "${PROJECT_ROOT}/config/env.default" /etc/personal-kvm/.env
fi

if [ ! -f /etc/personal-kvm/.env ]; then
    DB_PASSWORD="$(od -An -N 24 -tx1 /dev/urandom | tr -d ' \n')"
    JWT_SECRET="$(od -An -N 48 -tx1 /dev/urandom | tr -d ' \n')"
    cat << EOF > /etc/personal-kvm/.env
APP_VERSION=0.0.7
DATABASE_URL=mysql+pymysql://pkvm_user:${DB_PASSWORD}@127.0.0.1:3306/personal_kvm?charset=utf8mb4
DB_PASSWORD=${DB_PASSWORD}
JWT_SECRET_KEY=${JWT_SECRET}
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=720
LIBVIRT_DEFAULT_URI=qemu:///system
LIBGUESTFS_BACKEND=direct
CONFIG_YAML_PATH=/etc/personal-kvm/config.yaml
FRONTEND_DIST_DIR=/opt/personal-kvm/frontend/dist
QEMU_EMULATOR_PATH=${QEMU_EMULATOR_PATH}
EOF
fi
chown root:pkvm /etc/personal-kvm/.env
chmod 0640 /etc/personal-kvm/.env

TLS_DIR=/etc/personal-kvm/tls
install -d -o root -g pkvm -m 0750 "${TLS_DIR}"
if [ -n "${PKVM_TLS_CERT:-}" ] && [ -n "${PKVM_TLS_KEY:-}" ]; then
    install -o root -g pkvm -m 0644 "${PKVM_TLS_CERT}" "${TLS_DIR}/server.crt"
    install -o root -g pkvm -m 0640 "${PKVM_TLS_KEY}" "${TLS_DIR}/server.key"
elif [ ! -s "${TLS_DIR}/server.crt" ] || [ ! -s "${TLS_DIR}/server.key" ]; then
    TLS_NAME="$(hostname -f 2>/dev/null || hostname)"
    openssl req -x509 -newkey rsa:3072 -sha256 -days 365 -nodes \
        -subj "/CN=${TLS_NAME}" -addext "subjectAltName=DNS:${TLS_NAME},DNS:$(hostname)" \
        -keyout "${TLS_DIR}/server.key" -out "${TLS_DIR}/server.crt"
    chown root:pkvm "${TLS_DIR}/server.key" "${TLS_DIR}/server.crt"
    chmod 0640 "${TLS_DIR}/server.key"
    chmod 0644 "${TLS_DIR}/server.crt"
    echo "[보안] 자체서명 TLS 인증서를 생성했습니다. 운영 환경에서는 PKVM_TLS_CERT/PKVM_TLS_KEY로 공인 인증서를 지정하십시오."
fi

set -a
# shellcheck disable=SC1091
. /etc/personal-kvm/.env
set +a

if [ -z "${DB_PASSWORD:-}" ]; then
    DB_PASSWORD="$(od -An -N 24 -tx1 /dev/urandom | tr -d ' \n')"
    sed -i "s#^DATABASE_URL=.*#DATABASE_URL=mysql+pymysql://pkvm_user:${DB_PASSWORD}@127.0.0.1:3306/personal_kvm?charset=utf8mb4#" /etc/personal-kvm/.env
    printf 'DB_PASSWORD=%s\n' "${DB_PASSWORD}" >> /etc/personal-kvm/.env
    echo "[마이그레이션] 기존 고정 DB 비밀번호 구성을 설치별 난수로 교체합니다."
fi
if grep -q '^JWT_SECRET_KEY=personal-kvm-super-secret' /etc/personal-kvm/.env; then
    JWT_SECRET="$(od -An -N 48 -tx1 /dev/urandom | tr -d ' \n')"
    sed -i "s/^JWT_SECRET_KEY=.*/JWT_SECRET_KEY=${JWT_SECRET}/" /etc/personal-kvm/.env
fi

ADMIN_PASSWORD_GENERATED=0
if [ -z "${ADMIN_PASS}" ]; then
    if [ -f /etc/personal-kvm/.initial_admin_password ]; then
        ADMIN_PASS="$(tr -d '\r\n' < /etc/personal-kvm/.initial_admin_password)"
    else
        ADMIN_PASS="$(od -An -N 18 -tx1 /dev/urandom | tr -d ' \n')"
        printf '%s\n' "${ADMIN_PASS}" > /etc/personal-kvm/.initial_admin_password
        chmod 0600 /etc/personal-kvm/.initial_admin_password
        ADMIN_PASSWORD_GENERATED=1
    fi
fi
# Keep a root-only recovery copy even when PKVM_ADMIN_PASS was supplied.  This
# guarantees that an interrupted installation does not make the initial login
# credentials unknowable.
printf '%s\n' "${ADMIN_PASS}" > /etc/personal-kvm/.initial_admin_password
chmod 0600 /etc/personal-kvm/.initial_admin_password

if [ "$(cd "${PROJECT_ROOT}" && pwd)" != "/opt/personal-kvm" ]; then
    for sub in backend frontend templates scripts database; do
        if [ -d "${PROJECT_ROOT}/${sub}" ]; then
            cp -rf "${PROJECT_ROOT}/${sub}" /opt/personal-kvm/
        fi
    done
fi
chmod +x /opt/personal-kvm/scripts/*.sh
chmod -R 0755 /opt/personal-kvm/frontend

CURRENT_STAGE="networks"
echo "[4/7] 4대 가상 네트워크 (virbr0 ~ virbr3) 구성..."
/opt/personal-kvm/scripts/setup_networks.sh "${PHYS_NIC}" "${HOST_BR_IP}" "${HOST_BR_GW}" /etc/personal-kvm/config.yaml

CURRENT_STAGE="database"
echo "[5/7] MySQL 8.0 데이터베이스 및 계정 초기화..."
mysql -u root <<MYSQL_EOF
CREATE DATABASE IF NOT EXISTS personal_kvm CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'pkvm_user'@'127.0.0.1' IDENTIFIED BY '${DB_PASSWORD}';
CREATE USER IF NOT EXISTS 'pkvm_user'@'localhost' IDENTIFIED BY '${DB_PASSWORD}';
ALTER USER 'pkvm_user'@'127.0.0.1' IDENTIFIED BY '${DB_PASSWORD}';
ALTER USER 'pkvm_user'@'localhost' IDENTIFIED BY '${DB_PASSWORD}';
GRANT ALL PRIVILEGES ON personal_kvm.* TO 'pkvm_user'@'127.0.0.1';
GRANT ALL PRIVILEGES ON personal_kvm.* TO 'pkvm_user'@'localhost';
FLUSH PRIVILEGES;
MYSQL_EOF

if [ -f /opt/personal-kvm/database/schema.sql ]; then
    mysql -u root personal_kvm < /opt/personal-kvm/database/schema.sql
fi
if [ "$(mysql -u root -NBe "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='personal_kvm' AND TABLE_NAME='admins' AND COLUMN_NAME='must_change_password'")" -eq 0 ]; then
    mysql -u root personal_kvm -e "ALTER TABLE admins ADD COLUMN must_change_password TINYINT(1) NOT NULL DEFAULT 1"
fi

CURRENT_STAGE="application"
echo "[6/7] FastAPI 백엔드 가상환경 설치 및 초기 시드 데이터(어드민, 4대 네트워크 IP Pool, 스토리지, OS 이미지) 적재..."
cd /opt/personal-kvm/backend
rm -rf venv
${PYTHON_BIN} -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

./venv/bin/alembic -c alembic.ini upgrade head
PKVM_ADMIN_USER="${ADMIN_USER}" PKVM_ADMIN_PASS="${ADMIN_PASS}" \
    ./venv/bin/python -m app.core.init_db
PKVM_DOWNLOAD_DEFAULT_IMAGE="${PKVM_DOWNLOAD_DEFAULT_IMAGE:-1}" \
    runuser -u pkvm --preserve-environment -- ./venv/bin/python -m app.core.bootstrap_images

CURRENT_STAGE="service-start"
echo "[7/7] Systemd(8000) 및 Nginx(80 & 8080) 서비스 등록 및 시작..."
install -D -m 0644 /etc/personal-kvm/systemd/pkvm-backend.service /etc/systemd/system/pkvm-backend.service
install -D -m 0644 /etc/personal-kvm/systemd/pkvm-tls-renew.service /etc/systemd/system/pkvm-tls-renew.service
install -D -m 0644 /etc/personal-kvm/systemd/pkvm-tls-renew.timer /etc/systemd/system/pkvm-tls-renew.timer
install -D -m 0644 /etc/personal-kvm/nginx/personal-kvm.conf /etc/nginx/conf.d/personal-kvm.conf
rm -f /etc/nginx/conf.d/default.conf 2>/dev/null || true

# /etc/nginx/nginx.conf 기본 server 블록이 80 포트를 가로채지 않도록 정리
if grep -Eq "default_server|^[[:space:]]*listen[[:space:]]+(\[::\]:)?80([[:space:];]|$)" /etc/nginx/nginx.conf 2>/dev/null; then
    cat << 'NGINX_MAIN' > /etc/nginx/nginx.conf
user nginx;
worker_processes auto;
error_log /var/log/nginx/error.log notice;
pid /run/nginx.pid;
include /usr/share/nginx/modules/*.conf;

events {
    worker_connections 1024;
}

http {
    log_format  main  '$remote_addr - $remote_user [$time_local] "$request" '
                      '$status $body_bytes_sent "$http_referer" '
                      '"$http_user_agent" "$http_x_forwarded_for"';
    access_log  /var/log/nginx/access.log  main;
    sendfile            on;
    tcp_nopush          on;
    keepalive_timeout   65;
    types_hash_max_size 4096;
    include             /etc/nginx/mime.types;
    default_type        application/octet-stream;
    include /etc/nginx/conf.d/*.conf;
}
NGINX_MAIN
fi

systemctl daemon-reload
systemctl enable pkvm-backend.service
systemctl enable --now pkvm-tls-renew.timer
systemctl restart pkvm-backend.service

# Configure and start the public entry point independently of the deeper KVM
# validation below. This also makes nginx configuration errors fail explicitly.
nginx -t
systemctl enable nginx
systemctl restart nginx

BACKEND_READY=0
for attempt in $(seq 1 30); do
    if curl --fail --silent http://127.0.0.1:8000/api/health/ready >/dev/null 2>&1; then
        BACKEND_READY=1
        break
    fi
    sleep 1
done
if [ "${BACKEND_READY}" -ne 1 ]; then
    echo "[오류] 백엔드가 30초 안에 정상 기동하지 못했습니다." >&2
    systemctl status pkvm-backend.service --no-pager || true
    journalctl -u pkvm-backend.service -n 100 --no-pager || true
    exit 1
fi

# Run libguestfs in the backend's actual mount namespace.  A plain shell test
# cannot detect systemd sandbox settings that hide /lib/modules from supermin.
PROTECT_KERNEL_MODULES="$(systemctl show pkvm-backend.service -p ProtectKernelModules --value | tr '[:upper:]' '[:lower:]')"
if [ "${PROTECT_KERNEL_MODULES}" != "no" ] && [ "${PROTECT_KERNEL_MODULES}" != "false" ]; then
    echo "[오류] pkvm-backend가 libguestfs 커널 모듈 읽기를 차단하고 있습니다." >&2
    exit 1
fi
BACKEND_PID="$(systemctl show pkvm-backend.service -p MainPID --value)"
if [ -z "${BACKEND_PID}" ] || [ "${BACKEND_PID}" = "0" ] || \
   ! nsenter -t "${BACKEND_PID}" -m runuser -u pkvm -- \
       env HOME=/var/lib/personal-kvm LIBGUESTFS_BACKEND=direct \
       libguestfs-test-tool > "${INSTALL_STATE_DIR}/libguestfs-test.log" 2>&1; then
    echo "[오류] 실제 백엔드 격리 환경에서 libguestfs 검증에 실패했습니다." >&2
    tail -n 100 "${INSTALL_STATE_DIR}/libguestfs-test.log" >&2 || true
    exit 1
fi

DEFAULT_IMAGE="/data/images/Rocky-9-GenericCloud-Base.latest.x86_64.qcow2"
VALIDATION_OVERLAY="/data/instances/.pkvm-install-validation.qcow2"
rm -f "${VALIDATION_OVERLAY}"
if [ ! -s "${DEFAULT_IMAGE}" ] || \
   ! runuser -u pkvm -- qemu-img create -f qcow2 -F qcow2 -b "${DEFAULT_IMAGE}" "${VALIDATION_OVERLAY}" || \
   ! nsenter -t "${BACKEND_PID}" -m runuser -u pkvm -- \
       env HOME=/var/lib/personal-kvm LIBGUESTFS_BACKEND=direct \
       /opt/personal-kvm/scripts/customize_guest.sh "${VALIDATION_OVERLAY}" pkvm-install-validation \
       > "${INSTALL_STATE_DIR}/virt-customize-test.log" 2>&1; then
    rm -f "${VALIDATION_OVERLAY}"
    echo "[오류] 기본 Rocky 이미지의 virt-customize 검증에 실패했습니다." >&2
    tail -n 100 "${INSTALL_STATE_DIR}/virt-customize-test.log" >&2 || true
    exit 1
fi
rm -f "${VALIDATION_OVERLAY}"

if systemctl is-active --quiet firewalld; then
    firewall-cmd --permanent --add-port=80/tcp
    firewall-cmd --permanent --add-port=8080/tcp
    firewall-cmd --permanent --add-port=443/tcp
    firewall-cmd --reload
fi
if command -v setsebool >/dev/null 2>&1; then
    setsebool -P httpd_can_network_connect 1
fi

HOST_IP="$(hostname -I | awk '{print $1}')"
cat > /etc/personal-kvm/install-summary <<EOF
Personal KVM v0.0.6
Console: https://${HOST_IP}/
Admin user: ${ADMIN_USER}
Password file: /etc/personal-kvm/.initial_admin_password
Installed: $(date --iso-8601=seconds)
EOF
chmod 0600 /etc/personal-kvm/install-summary
echo "=================================================================="
echo " [설치 완료] 퍼스널 KVM 가상화 서비스 v0.0.6 정상 구동 중!"
echo " - 관리 콘솔           : https://${HOST_IP}/"
echo " - HTTP 80/8080 요청은 HTTPS로 전환됩니다."
echo " - 백엔드 8000 포트는 127.0.0.1에서만 수신합니다."
echo " - API Swagger 문서    : https://${HOST_IP}/docs"
echo " - 초기 어드민 계정    : ${ADMIN_USER} / ${ADMIN_PASS}"
echo " - 설치 정보 재확인    : sudo cat /etc/personal-kvm/install-summary"
if [ "${ADMIN_PASSWORD_GENERATED}" -eq 1 ]; then
    echo " - 위 초기 비밀번호는 자동 생성되었습니다. 지금 안전한 곳에 보관하십시오."
fi
INSTALL_COMPLETED=1
rm -f "${INSTALL_STATE_DIR}/failed-stage"
echo "=================================================================="
