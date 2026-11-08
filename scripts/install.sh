#!/bin/bash
# scripts/install.sh : 퍼스널 KVM 가상화 서비스 v0.1.9 설치
set -euo pipefail

if [ "${EUID}" -ne 0 ]; then
    echo "[오류] install.sh는 반드시 root 권한(sudo)으로 실행해야 합니다."
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "${SCRIPT_DIR}")"
# Strict, read-only CPU/OS checks run before any host state change.
bash "${SCRIPT_DIR}/preflight_check.sh"

if [ ! -r /etc/os-release ]; then
    echo "[오류] /etc/os-release를 읽을 수 없습니다." >&2; exit 1
fi
# shellcheck disable=SC1091
. /etc/os-release
ROCKY_MAJOR="${VERSION_ID%%.*}"
if [ "${ID:-}" != "rocky" ] || { [ "${ROCKY_MAJOR}" != "9" ] && [ "${ROCKY_MAJOR}" != "10" ]; }; then
    echo "[오류] Rocky Linux 9.x 또는 10.x만 지원합니다. 현재: ${PRETTY_NAME:-unknown}" >&2; exit 1
fi
if ! grep -Eq '(vmx|svm)' /proc/cpuinfo; then
    echo "[오류] CPU 가상화 확장(Intel VT-x/AMD-V)을 사용할 수 없습니다." >&2; exit 1
fi
if [ "$(df -Pk / | awk 'NR==2 {print $4}')" -lt 5242880 ]; then
    echo "[오류] 루트 파일시스템에 최소 5GB의 여유 공간이 필요합니다." >&2; exit 1
fi


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
require_project_file "config/systemd/pkvm-lb-agent.service"
require_project_file "config/systemd/pkvm-tls-renew.service"
require_project_file "config/systemd/pkvm-tls-renew.timer"
require_project_file "config/nginx/personal-kvm.conf"
require_project_file "database/schema.sql"
require_project_file "templates/grub_default.tpl"
require_project_file "scripts/customize_guest.sh"
require_project_file "scripts/setup_networks.sh"
require_project_file "scripts/ensure_networks.sh"
require_project_file "scripts/renew_tls.sh"
require_project_file "scripts/preflight_check.sh"
require_project_file "config/config.yaml"

ADMIN_USER="${PKVM_ADMIN_USER:-admin}"
ADMIN_PASS="${PKVM_ADMIN_PASS:-}"
# Consume the complete producer output: with `set -o pipefail`, an early awk
# exit can give `ip` SIGPIPE and make an otherwise successful install stop.
DEFAULT_ROUTE_IFACE="$(ip route | awk '/default/ && !found {print $5; found=1}')"
detect_physical_nic() {
    local route_iface="$1" port_path
    if [ -n "${route_iface}" ] && [ -d "/sys/class/net/${route_iface}/brif" ]; then
        for port_path in "/sys/class/net/${route_iface}/brif/"*; do
            [ -e "${port_path}" ] || continue
            basename "${port_path}"
            return 0
        done
    fi
    printf '%s\n' "${route_iface}"
}
PHYS_NIC="${PKVM_PHYS_NIC:-$(detect_physical_nic "${DEFAULT_ROUTE_IFACE}")}"
PHYS_NIC="${PHYS_NIC:-enp1s0}"
HOST_BR_IP="${PKVM_HOST_BR_IP:-$(ip -4 -o addr show dev "${DEFAULT_ROUTE_IFACE:-${PHYS_NIC}}" 2>/dev/null | awk 'NF && !found {print $4; found=1}')}"
HOST_BR_IP="${HOST_BR_IP:-192.168.0.40/24}"
HOST_BR_GW="${PKVM_HOST_BR_GW:-$(ip route | awk '/default/ && !found {print $3; found=1}')}"
HOST_BR_GW="${HOST_BR_GW:-192.168.0.1}"

echo "=================================================================="
echo " 퍼스널 KVM 가상화 서비스 v0.1.9 설치 시작 (Rocky Linux 9.x / 10.x)"
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
        echo '[안내] 초기 관리자 비밀번호는 로그에 표시하지 않습니다.' >&2
        echo '[안내] 필요 시 sudo cat /etc/personal-kvm/.initial_admin_password' >&2
    fi
}
trap on_install_exit EXIT

CURRENT_STAGE="packages"
echo "[1/7] Rocky ${ROCKY_MAJOR}.x 별 필수 패키지 및 실행 도구 확인..."

# DB: Rocky 10.2에서는 사용자 서버 repoquery 결과에 따라 MariaDB를 우선.
# Rocky 9에서는 저장소/기존 설치 상태를 검사해 MySQL 또는 MariaDB를 결정.
repo_has_package() {
    local package="$1"
    dnf -q repoquery --qf '%{name}' "${package}" 2>/dev/null | grep -Fxq "${package}"
}
installed_or_repo() {
    rpm -q "$1" >/dev/null 2>&1 || repo_has_package "$1"
}
choose_package() {
    local pkg
    for pkg in "$@"; do
        if installed_or_repo "$pkg"; then printf '%s\n' "$pkg"; return 0; fi
    done
    return 1
}
install_verified_package() {
    local package="$1"
    installed_or_repo "$package" || { echo "[오류] 활성 저장소/설치 목록에 없음: $package" >&2; exit 1; }
    if ! rpm -q "$package" >/dev/null 2>&1; then dnf install -y "$package"; fi
}
install_verified_dependency() {
    local executable="$1"; shift
    if command -v "$executable" >/dev/null 2>&1; then return 0; fi
    local candidate
    candidate="$(choose_package "$@")" || {
        echo "[오류] '$executable' 명령/패키지 없음 (후보: $*)" >&2; exit 1;
    }
    install_verified_package "$candidate"
    command -v "$executable" >/dev/null 2>&1 || {
        echo "[오류] $candidate 설치 후 '$executable' 실행 파일 없음" >&2; exit 1;
    }
}

node_version_is_supported() {
    command -v node >/dev/null 2>&1 &&
        node -e 'const [major,minor]=process.versions.node.split(".").map(Number); process.exit((major===20 && minor>=19)||(major===22 && minor>=12)||(major>22)?0:1)'
}

ensure_supported_nodejs() {
    if node_version_is_supported && command -v npm >/dev/null 2>&1; then
        return 0
    fi

    if [ "${ROCKY_MAJOR}" = "9" ]; then
        # Rocky 9 defaults to Node.js 16 when no AppStream module is selected,
        # while the current Vite toolchain requires Node.js 20.19+ or 22.12+.
        dnf -y module reset nodejs
        dnf -y module enable nodejs:22
        dnf -y install --allowerasing nodejs npm
    else
        install_verified_package nodejs
        if ! command -v npm >/dev/null 2>&1; then
            if installed_or_repo nodejs-npm; then
                dnf install -y nodejs-npm
            elif installed_or_repo npm; then
                dnf install -y npm
            fi
        fi
    fi

    if ! node_version_is_supported || ! command -v npm >/dev/null 2>&1; then
        local installed_version
        installed_version="$(node -p 'process.versions.node' 2>/dev/null || echo unavailable)"
        echo "[오류] 지원되는 Node.js/npm 설치에 실패했습니다. Node.js: ${installed_version}" >&2
        exit 1
    fi
}

# 설치가 실패해도 임의 저장소를 활성화하거나 해당 패키지를 추정 설치하지 않음.
# Rocky 9에서는 EPEL/CRB 활성화가 필요한 환경이면 운영자가 먼저 설정해야 함.
core_packages=(qemu-kvm libvirt virt-install cloud-utils-growpart python3-devel)
if [ "$ROCKY_MAJOR" = 10 ]; then
    core_packages+=(guestfs-tools xorriso nodejs)
else
    guestfs_package="$(choose_package guestfs-tools libguestfs-tools)" || {
        echo '[오류] Rocky 9 guestfs-tools/libguestfs-tools 제공 확인 필요' >&2; exit 1;
    }
    core_packages+=("$guestfs_package")
fi
for package in "${core_packages[@]}"; do install_verified_package "$package"; done
install_verified_dependency python3 python3
install_verified_dependency pip3 python3-pip
install_verified_dependency virsh libvirt-client libvirt
install_verified_dependency qemu-img qemu-img qemu-kvm
install_verified_dependency virt-customize guestfs-tools libguestfs-tools
install_verified_dependency supermin supermin
install_verified_dependency libguestfs-test-tool libguestfs
install_verified_dependency nginx nginx
install_verified_dependency curl curl
install_verified_dependency wget wget
install_verified_dependency tar tar
install_verified_dependency unzip unzip
install_verified_dependency jq jq
install_verified_dependency openssl openssl
install_verified_dependency firewall-cmd firewalld
install_verified_dependency setsebool policycoreutils-python-utils
install_verified_dependency nmcli NetworkManager
install_verified_dependency ssh-keygen openssh-clients
install_verified_dependency nsenter util-linux
install_verified_dependency modprobe kmod
# Rocky 9에는 genisoimage 또는 cloud-localds만 있을 수 있음.
# 이미 지원되는 seed 도구가 있으면 xorriso를 추가 요구하지 않음.
if ! command -v cloud-localds >/dev/null 2>&1 && ! command -v genisoimage >/dev/null 2>&1; then
    install_verified_dependency xorriso xorriso
fi

# xorriso가 설치된 환경에서는 VM 생성기의 기본 탐색에 직접 포함됨.
# 예전 버전의 genisoimage 명령을 덮어쓰거나 임의 setuid 변경하지 않음.
if ! command -v cloud-localds >/dev/null 2>&1 && \
   ! command -v genisoimage >/dev/null 2>&1 && \
   ! command -v xorriso >/dev/null 2>&1; then
    echo '[오류] cloud-init seed 생성을 위한 ISO 도구가 없습니다.' >&2; exit 1
fi
rpm -qa | sort > "${INSTALL_STATE_DIR}/packages.after"
comm -13 "${INSTALL_STATE_DIR}/packages.before" "${INSTALL_STATE_DIR}/packages.after" > "${INSTALL_STATE_DIR}/packages.added"
PYTHON_BIN=python3
PYTHON_VERSION="$($PYTHON_BIN -c 'import sys; print(".".join(map(str,sys.version_info[:3])))')"
# 기존 고정 requirements는 Python 3.9 이상을 가정함. Rocky 10 실측은 3.12.13.
if ! "$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3,9) else 1)'; then
    echo "[오류] Python 3.9 이상 필요: ${PYTHON_VERSION}" >&2; exit 1
fi
if ! "$PYTHON_BIN" -c 'import yaml' >/dev/null 2>&1; then
    install_verified_package python3-pyyaml
    "$PYTHON_BIN" -c 'import yaml' || { echo '[오류] PyYAML import 실패' >&2; exit 1; }
fi
if ! "$PYTHON_BIN" -m venv --help >/dev/null 2>&1; then
    install_verified_dependency 'virtualenv' python3-virtualenv
    echo '[오류] python3 -m venv를 제공하는 패키지 확인 및 설치 필요' >&2
    exit 1
fi
# Node 22은 Rocky 10.2에서 실제 확인됨. Rocky 9는 활성 저장소 기준으로 선택.
ensure_supported_nodejs
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
if ! command -v node >/dev/null 2>&1; then
    echo "[오류] nodejs 설치 후 node 명령을 찾을 수 없습니다." >&2
    exit 1
fi
# Rocky 10.2: nodejs may provide npm itself; otherwise resolve the verified name.
if ! command -v npm >/dev/null 2>&1; then
    if installed_or_repo nodejs-npm; then
        dnf install -y nodejs-npm
    elif installed_or_repo npm; then
        dnf install -y npm
    else
        echo "[오류] nodejs 설치 후 npm 미제공, nodejs-npm/npm 패키지도 저장소에 없습니다." >&2
        exit 1
    fi
fi
command -v npm >/dev/null 2>&1 || { echo '[오류] npm 명령을 찾을 수 없습니다.' >&2; exit 1; }
NODE_VERSION="$(node -p 'process.versions.node' 2>/dev/null || echo 0.0.0)"
if ! node_version_is_supported; then
    echo "[오류] 프런트엔드 빌드는 Node.js 20.19+ 또는 22.12+가 필요합니다. 현재: ${NODE_VERSION}" >&2
    exit 1
fi
(cd "${PROJECT_ROOT}/frontend" && npm ci --no-audit --no-fund && npm run build)
if [ ! -s "${PROJECT_ROOT}/frontend/dist/index.html" ]; then
    echo "[오류] 프런트엔드 빌드 결과가 없습니다: frontend/dist/index.html" >&2
    exit 1
fi

CURRENT_STAGE="base-services"
echo "[2/7] 설치된 libvirt 데몬과 OS별 DB 활성화..."
systemctl daemon-reload
unit_exists() {
    local state
    state="$(systemctl show -p LoadState --value "$1" 2>/dev/null || true)"
    [ -n "$state" ] && [ "$state" != "not-found" ]
}
LIBVIRT_UNITS=()
if unit_exists virtqemud.socket; then
    for unit in virtqemud.socket virtnetworkd.socket virtstoraged.socket virtproxyd.socket; do
        if unit_exists "$unit"; then
            LIBVIRT_UNITS+=("$unit")
        else
            echo "[주의] 설치된 libvirt에 ${unit} 없음: 건너뜀"
        fi
    done
elif unit_exists libvirtd.service; then
    LIBVIRT_UNITS=(libvirtd.service)
else
    echo "[오류] libvirt 서비스/소켓 유닛을 확인할 수 없습니다." >&2
    exit 1
fi
for unit in "${LIBVIRT_UNITS[@]}"; do
    systemctl enable --now "$unit"
done
# 기존 설치 DB를 우선 유지한다. Rocky 10.2는 mariadb-server가 확인됨.
DB_SERVER_PACKAGE=''
DB_UNIT=''
if systemctl is-active --quiet mariadb.service || rpm -q mariadb-server >/dev/null 2>&1; then
    DB_SERVER_PACKAGE=mariadb-server; DB_UNIT=mariadb.service
elif systemctl is-active --quiet mysqld.service || rpm -q mysql-server >/dev/null 2>&1; then
    DB_SERVER_PACKAGE=mysql-server; DB_UNIT=mysqld.service
elif [ "$ROCKY_MAJOR" = 10 ]; then
    DB_SERVER_PACKAGE="$(choose_package mariadb-server)" || { echo '[오류] Rocky 10 MariaDB 미제공' >&2; exit 1; }
    DB_UNIT=mariadb.service
else
    DB_SERVER_PACKAGE="$(choose_package mysql-server mariadb-server)" || { echo '[오류] Rocky 9 DB 서버 패키지 미제공' >&2; exit 1; }
    if [ "$DB_SERVER_PACKAGE" = mysql-server ]; then DB_UNIT=mysqld.service; else DB_UNIT=mariadb.service; fi
fi
install_verified_package "$DB_SERVER_PACKAGE"
systemctl daemon-reload
unit_exists "$DB_UNIT" || { echo "[오류] DB 유닛 누락: $DB_UNIT" >&2; exit 1; }
systemctl enable --now "$DB_UNIT"
if command -v mariadb >/dev/null 2>&1; then DB_CLI=mariadb
elif command -v mysql >/dev/null 2>&1; then DB_CLI=mysql
else install_verified_dependency mariadb mariadb; DB_CLI=mariadb; fi
if ! "$DB_CLI" -u root -NBe 'SELECT 1' >/dev/null 2>&1; then
    echo '[오류] DB root 로컬 소켓 인증 실패: 관리자 조치 필요' >&2; exit 1
fi
echo "[매핑] Rocky=${ROCKY_MAJOR}, DB=${DB_SERVER_PACKAGE}/${DB_UNIT}/${DB_CLI}, libvirt=${LIBVIRT_UNITS[*]}, Python=${PYTHON_VERSION}"
printf 'PKVM_OS_MAJOR=%q\nPKVM_DB_UNIT=%q\nPKVM_DB_CLI=%q\nPKVM_LIBVIRT_UNIT=%q\n' \
    "$ROCKY_MAJOR" "$DB_UNIT" "$DB_CLI" "${LIBVIRT_UNITS[0]}" > "${INSTALL_STATE_DIR}/os-runtime.env"
chmod 0600 "${INSTALL_STATE_DIR}/os-runtime.env"

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
install -d -o pkvm -g pkvm -m 0700 /var/lib/personal-kvm/tmp /var/lib/personal-kvm/cache /var/lib/personal-kvm/cache/libguestfs
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
# compute.yaml is the authoritative installation contract. Historically only
# compute_install.sh read it, allowing install.sh to deploy the sample
# config/config.yaml (including kvm.test). Normalize both entry points here.
COMPUTE_SPEC="${PKVM_COMPUTE_CONFIG:-${PROJECT_ROOT}/compute.yaml}"
if [ -r "$COMPUTE_SPEC" ]; then
    COMPUTE_EXPORTS="$(python3 "${PROJECT_ROOT}/scripts/validate_compute.py" "$COMPUTE_SPEC")"
    eval "$COMPUTE_EXPORTS"
fi
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

if [ -n "${PKVM_PRIVATE_DOMAIN:-}" ] && [ -n "${PKVM_EXTERNAL_DOMAIN:-}" ]; then
    python3 - /etc/personal-kvm/config.yaml "$PKVM_PRIVATE_DOMAIN" "$PKVM_EXTERNAL_DOMAIN" "$PKVM_DNS_REVERSE_ZONE" <<'PY_COMPUTE_DNS'
from pathlib import Path
import sys,yaml
p=Path(sys.argv[1]); private,external,reverse=sys.argv[2:]
with p.open(encoding='utf-8') as stream: data=yaml.safe_load(stream) or {}
dns=data.setdefault('dns',{})
dns.update(enabled=True,server='127.0.0.1',key_file='/etc/named/pkvm/pkvm-ddns.key',
           forward_zone=private,private_domain=private,external_domain=external,
           reverse_zone=reverse,ttl=300)
with p.open('w',encoding='utf-8') as stream:
    yaml.safe_dump(data,stream,allow_unicode=True,sort_keys=False)
PY_COMPUTE_DNS
fi

if [ -n "${PKVM_PUBLIC_CIDR:-}" ]; then
    python3 - /etc/personal-kvm/config.yaml "$PKVM_PUBLIC_CIDR" "$PKVM_NAT_CIDR" "$PKVM_INTERNAL_CIDR" "$PKVM_MGMT_CIDR" "$PKVM_IP_ALLOCATION_STRATEGY" <<'PY_COMPUTE_NETWORKS'
from pathlib import Path
import ipaddress,sys,yaml
p=Path(sys.argv[1]); cidrs=sys.argv[2:6]; strategy=sys.argv[6]
with p.open(encoding='utf-8') as stream: data=yaml.safe_load(stream) or {}
data.setdefault('service',{})['ip_allocation_strategy']=strategy
keys=('bridged','nat','internal','management')
for key,cidr in zip(keys,cidrs):
    network=ipaddress.ip_network(cidr,strict=True)
    item=data['networks'][key]
    item['cidr']=str(network)
    usable=network.num_addresses-2
    if usable < 4: raise SystemExit(f'{key} CIDR is too small')
    first=network.network_address+1
    if key == 'nat': item['gateway']=str(first)
    elif key == 'internal': item['gateway']=None
    elif key == 'management':
        item['gateway']=None; item['host_bind_ip']=str(first)
    start_offset=min(101,network.num_addresses-2)
    end_offset=min(250,network.num_addresses-2)
    item['pool_start']=str(network.network_address+start_offset)
    item['pool_end']=str(network.network_address+end_offset)
data['generated']={'source':'compute.yaml','schema_version':1}
with p.open('w',encoding='utf-8') as stream:
    yaml.safe_dump(data,stream,allow_unicode=True,sort_keys=False)
PY_COMPUTE_NETWORKS
fi

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
# Guests must query the local Bind9 authority rather than public resolvers.
# Use the externally bridged host address because it is reachable from every
# host-owned libvirt bridge, including networks whose dnsmasq uses no-resolv.
python3 - /etc/personal-kvm/config.yaml <<'PY_GUEST_DNS'
from pathlib import Path
import ipaddress,os,sys,tempfile,yaml,grp
p=Path(sys.argv[1])
with p.open(encoding='utf-8') as stream: data=yaml.safe_load(stream) or {}
client_ip=str(ipaddress.ip_interface(data['networks']['bridged']['host_ip_cidr']).ip)
data.setdefault('dns',{})['client_server']=client_ip
for network in data['networks'].values(): network['dns']=client_ip
fd,tmp=tempfile.mkstemp(dir=str(p.parent),prefix='.config-guest-dns-',text=True)
try:
    with os.fdopen(fd,'w',encoding='utf-8') as stream:
        yaml.safe_dump(data,stream,allow_unicode=True,sort_keys=False)
        stream.flush(); os.fsync(stream.fileno())
    os.chmod(tmp,0o640); os.chown(tmp,0,grp.getgrnam('pkvm').gr_gid); os.replace(tmp,p)
finally:
    if os.path.exists(tmp): os.unlink(tmp)
PY_GUEST_DNS
/opt/personal-kvm/scripts/setup_networks.sh "${PHYS_NIC}" "${HOST_BR_IP}" "${HOST_BR_GW}" /etc/personal-kvm/config.yaml

# A configured dns.enabled=true is an explicit request to provision the local
# authoritative service. PKVM_SETUP_BIND9 remains available for env-only installs.
mapfile -t DNS_CONFIG < <(python3 - /etc/personal-kvm/config.yaml <<'PY_DNS_CONFIG'
import sys,yaml
with open(sys.argv[1], encoding='utf-8') as stream:
    dns=(yaml.safe_load(stream) or {}).get('dns') or {}
print('1' if dns.get('enabled') else '0')
for key in ('forward_zone','reverse_zone','server','client_server'):
    print(dns.get(key) or '')
PY_DNS_CONFIG
)
if [ "${PKVM_SETUP_BIND9:-${DNS_CONFIG[0]}}" = "1" ]; then
    CURRENT_STAGE="bind9"
    echo "[DNS] Bind9 TSIG 동적 DNS 설치 및 구성..."
    DNS_FORWARD="${PKVM_DNS_FORWARD_ZONE:-${DNS_CONFIG[1]}}"
    DNS_REVERSE="${PKVM_DNS_REVERSE_ZONE:-${DNS_CONFIG[2]}}"
    DNS_SERVER="${DNS_CONFIG[3]}"
    DNS_ZONE_NS="${PKVM_DNS_SERVER_IP:-${DNS_CLIENT_SERVER:-${DNS_CONFIG[3]}}}"
    DNS_CLIENT_SERVER="${PKVM_DNS_CLIENT_IP:-${DNS_CONFIG[4]}}"
    [ -n "$DNS_FORWARD" ] && [ -n "$DNS_REVERSE" ] && [ -n "$DNS_SERVER" ] || {
        echo '[오류] dns.enabled=true이면 server/forward_zone/reverse_zone이 필요합니다.' >&2; exit 1;
    }
    mapfile -t DNS_CLIENT_CIDRS < <(python3 - /etc/personal-kvm/config.yaml <<'PY_DNS_CIDRS'
import sys,yaml
with open(sys.argv[1],encoding='utf-8') as stream: data=yaml.safe_load(stream) or {}
for item in (data.get('networks') or {}).values(): print(item['cidr'])
PY_DNS_CIDRS
)
    bash /opt/personal-kvm/scripts/setup_bind9.sh "$DNS_FORWARD" "$DNS_REVERSE" "$DNS_ZONE_NS" "$DNS_CLIENT_SERVER" "${DNS_CLIENT_CIDRS[@]}"
    if [ -n "${PKVM_PRIVATE_DOMAIN:-}" ] && [ -n "${PKVM_EXTERNAL_DOMAIN:-}" ]; then
        python3 /opt/personal-kvm/scripts/setup_split_dns.py \
            --private "$PKVM_PRIVATE_DOMAIN" --external "$PKVM_EXTERNAL_DOMAIN" \
            --reverse "$DNS_REVERSE" --server "$DNS_ZONE_NS" \
            --public "${PKVM_PUBLIC_CIDR}" \
            --nat "$(python3 -c 'import yaml; print(yaml.safe_load(open("/etc/personal-kvm/config.yaml"))["networks"]["nat"]["cidr"])')" \
            --internal "${PKVM_INTERNAL_CIDR}" --management "${PKVM_MGMT_CIDR}" \
            --listen "$DNS_CLIENT_SERVER" --apply
    fi
    python3 - /etc/personal-kvm/config.yaml "$DNS_FORWARD" "$DNS_REVERSE" "$DNS_SERVER" "$DNS_CLIENT_SERVER" <<'PY_BIND9'
from pathlib import Path
import os,sys,tempfile,yaml
p=Path(sys.argv[1]); forward,reverse,server,client_server=sys.argv[2:]
with p.open(encoding='utf-8') as stream:
    data=yaml.safe_load(stream)
data['dns']={'enabled':True,'server':server,'key_file':'/etc/named/pkvm/pkvm-ddns.key',
             'client_server':client_server,'forward_zone':forward,'reverse_zone':reverse,'ttl':300}
if os.environ.get('PKVM_PRIVATE_DOMAIN'):
    data['dns']['private_domain']=os.environ['PKVM_PRIVATE_DOMAIN']
if os.environ.get('PKVM_EXTERNAL_DOMAIN'):
    data['dns']['external_domain']=os.environ['PKVM_EXTERNAL_DOMAIN']
fd,path=tempfile.mkstemp(dir=str(p.parent),prefix='.config-bind9-',text=True)
try:
    with os.fdopen(fd,'w',encoding='utf-8') as stream:
        yaml.safe_dump(data,stream,allow_unicode=True,sort_keys=False)
        stream.flush(); os.fsync(stream.fileno())
    os.chmod(path,0o640)
    import grp
    os.chown(path,0,grp.getgrnam('pkvm').gr_gid)
    os.replace(path,p)
finally:
    if os.path.exists(path): os.unlink(path)
PY_BIND9
fi

CURRENT_STAGE="nfs"
if [ "${PKVM_SETUP_NFS:-1}" = "1" ]; then
echo "[NFS] 관리망 공유 볼륨 서버 및 export 구성..."
mapfile -t NFS_CONFIG < <(python3 - /etc/personal-kvm/config.yaml <<'PY_NFS_CONFIG'
import sys,yaml
with open(sys.argv[1], encoding='utf-8') as stream:
    data=yaml.safe_load(stream) or {}
print((data.get('nfs') or {}).get('NFS_DIR') or '/data/nfs')
print(data['networks']['management']['cidr'])
PY_NFS_CONFIG
)
NFS_DIR="${PKVM_NFS_DIR:-${NFS_CONFIG[0]}}"
NFS_CIDR="${PKVM_MGMT_CIDR:-${NFS_CONFIG[1]}}"
bash /opt/personal-kvm/scripts/setup_nfs.sh "$NFS_DIR" "$NFS_CIDR"
python3 - /etc/personal-kvm/config.yaml "$NFS_DIR" <<'PY_NFS_PERSIST'
from pathlib import Path
import os,sys,tempfile,yaml,grp
p=Path(sys.argv[1]); nfs_dir=sys.argv[2]
with p.open(encoding='utf-8') as stream: data=yaml.safe_load(stream) or {}
data['nfs']={'NFS_DIR':nfs_dir}
fd,tmp=tempfile.mkstemp(dir=str(p.parent),prefix='.config-nfs-',text=True)
try:
    with os.fdopen(fd,'w',encoding='utf-8') as stream:
        yaml.safe_dump(data,stream,allow_unicode=True,sort_keys=False)
        stream.flush(); os.fsync(stream.fileno())
    os.chmod(tmp,0o640); os.chown(tmp,0,grp.getgrnam('pkvm').gr_gid); os.replace(tmp,p)
finally:
    if os.path.exists(tmp): os.unlink(tmp)
PY_NFS_PERSIST
else
    echo "[NFS] compute.yaml에서 비활성화됨"
fi

CURRENT_STAGE="load-balancers"
echo "[LB] HAProxy L4 및 Traefik L7 런타임 구성..."
if [ "${PKVM_SETUP_HAPROXY:-1}" = "1" ]; then
    bash /opt/personal-kvm/scripts/setup_lb.sh haproxy "${PKVM_HAPROXY_PORT:-8404}"
fi
if [ "${PKVM_SETUP_TRAEFIK:-1}" = "1" ]; then
    bash /opt/personal-kvm/scripts/setup_lb.sh traefik "${PKVM_TRAEFIK_PORT:-8443}"
fi

CURRENT_STAGE="database"
echo "[5/7] MariaDB 데이터베이스 및 계정 초기화..."
"${DB_CLI}" -u root <<MYSQL_EOF
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
    "${DB_CLI}" -u root personal_kvm < /opt/personal-kvm/database/schema.sql
fi
if [ "$("${DB_CLI}" -u root -NBe "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='personal_kvm' AND TABLE_NAME='admins' AND COLUMN_NAME='must_change_password'")" -eq 0 ]; then
    "${DB_CLI}" -u root personal_kvm -e "ALTER TABLE admins ADD COLUMN must_change_password TINYINT(1) NOT NULL DEFAULT 1"
fi

CURRENT_STAGE="application"
echo "[6/7] FastAPI 백엔드 가상환경 설치 및 초기 시드 데이터(어드민, 4대 네트워크 IP Pool, 스토리지, OS 이미지) 적재..."
cd /opt/personal-kvm/backend
rm -rf venv
${PYTHON_BIN} -m venv venv
./venv/bin/pip install --upgrade pip
# Pre-resolve every dependency against binary wheels before modifying venv packages.
./venv/bin/pip install --dry-run --only-binary=:all: -r requirements.txt
./venv/bin/pip install --only-binary=:all: -r requirements.txt

./venv/bin/alembic -c alembic.ini upgrade head
PKVM_ADMIN_USER="${ADMIN_USER}" PKVM_ADMIN_PASS="${ADMIN_PASS}" \
    ./venv/bin/python -m app.core.init_db
PKVM_DOWNLOAD_DEFAULT_IMAGE="${PKVM_DOWNLOAD_DEFAULT_IMAGE:-1}" \
    runuser -u pkvm --preserve-environment -- ./venv/bin/python -m app.core.bootstrap_images

CURRENT_STAGE="service-start"
echo "[7/7] Systemd(8000) 및 Nginx(80 & 8080) 서비스 등록 및 시작..."
install -D -m 0644 /etc/personal-kvm/systemd/pkvm-backend.service /etc/systemd/system/pkvm-backend.service
# Optional libvirt/kvm group names vary by package build; do not require absent groups.
if ! getent group kvm >/dev/null 2>&1 || ! getent group libvirt >/dev/null 2>&1; then
    groups=()
    for group in kvm libvirt; do getent group "$group" >/dev/null 2>&1 && groups+=("$group"); done
    sed -i '/^SupplementaryGroups=/d' /etc/systemd/system/pkvm-backend.service
    if [ "${#groups[@]}" -gt 0 ]; then
        sed -i "/^Group=pkvm$/a SupplementaryGroups=${groups[*]}" /etc/systemd/system/pkvm-backend.service
    fi
fi
# config/systemd 원본은 수정하지 않는다. 설치된 사본만 실제 유닛에 맞춘다.
# 기존 mysqld.service 필수 의존성을 MariaDB로 전환하며, 존재하지 않는 libvirt 유닛은 제거한다.
sed -i -e "s/mysqld\.service/${DB_UNIT}/g" -e "s/mariadb\.service/${DB_UNIT}/g" /etc/systemd/system/pkvm-backend.service
for candidate in virtqemud.socket virtnetworkd.socket virtstoraged.socket virtproxyd.socket; do
    if ! unit_exists "$candidate"; then
        sed -i "s/[[:space:]]${candidate}//g" /etc/systemd/system/pkvm-backend.service
    fi
done
# 모듈러 유닛이 없고 단일 libvirtd가 존재하면 시작 순서에 포함한다.
if [ "${LIBVIRT_UNITS[0]}" = 'libvirtd.service' ]; then
    sed -i -e '/^Wants=/s/$/ libvirtd.service/' \
           -e '/^After=/s/$/ libvirtd.service/' /etc/systemd/system/pkvm-backend.service
fi
install -D -m 0644 /etc/personal-kvm/systemd/pkvm-tls-renew.service /etc/systemd/system/pkvm-tls-renew.service
install -D -m 0644 /etc/personal-kvm/systemd/pkvm-tls-renew.timer /etc/systemd/system/pkvm-tls-renew.timer
install -D -m 0644 /etc/personal-kvm/systemd/pkvm-lb-agent.service /etc/systemd/system/pkvm-lb-agent.service
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
systemctl enable pkvm-lb-agent.service
systemctl restart pkvm-lb-agent.service
for _ in $(seq 1 40); do
    if systemctl is-active --quiet pkvm-lb-agent.service && [ -S /run/pkvm-lb-agent/control.sock ]; then
        break
    fi
    sleep 0.25
done
if ! systemctl is-active --quiet pkvm-lb-agent.service || [ ! -S /run/pkvm-lb-agent/control.sock ]; then
    echo '[오류] LB Agent 기동 또는 보호된 UNIX 소켓 생성 실패' >&2
    journalctl -u pkvm-lb-agent.service -n 50 --no-pager >&2 || true
    exit 1
fi
systemctl enable --now pkvm-tls-renew.timer
systemctl restart pkvm-backend.service

# Configure and start the public entry point independently of the deeper KVM
# validation below. This also makes nginx configuration errors fail explicitly.
nginx -t
systemctl enable nginx
systemctl restart nginx

BACKEND_READY=0
for attempt in $(seq 1 30); do
    if curl --fail --silent --connect-timeout 1 --max-time 3 \
        http://127.0.0.1:8000/api/health/ready >/dev/null 2>&1; then
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
# Regression guard: previously virt-customize failed because /tmp was read-only
# under ProtectSystem=strict. Test paths from the REAL backend mount namespace.
if [ -z "${BACKEND_PID}" ] || [ "${BACKEND_PID}" = "0" ]; then
    echo '[오류] 백엔드 PID를 찾지 못해 libguestfs 파일시스템 검사 불가' >&2
    exit 1
fi
if ! nsenter -t "${BACKEND_PID}" -m runuser -u pkvm -- \
    env TMPDIR=/var/lib/personal-kvm/tmp \
        LIBGUESTFS_TMPDIR=/var/lib/personal-kvm/tmp \
        LIBGUESTFS_CACHEDIR=/var/lib/personal-kvm/cache/libguestfs \
    bash -c 'set -e; for base in /tmp /var/tmp "$TMPDIR" "$LIBGUESTFS_TMPDIR" "$LIBGUESTFS_CACHEDIR"; do d="$(mktemp -d "${base%/}/pkvm-guestfs-XXXXXXXX")"; rmdir "$d"; done' \
    > "${INSTALL_STATE_DIR}/guestfs-path-test.log" 2>&1; then
    echo '[오류] 백엔드 격리 환경에서 libguestfs 임시 디렉터리 쓰기 검사 실패' >&2
    cat "${INSTALL_STATE_DIR}/guestfs-path-test.log" >&2 || true
    exit 1
fi
if [ -z "${BACKEND_PID}" ] || [ "${BACKEND_PID}" = "0" ] || \
   ! nsenter -t "${BACKEND_PID}" -m runuser -u pkvm -- \
       env HOME=/var/lib/personal-kvm TMPDIR=/var/lib/personal-kvm/tmp \
           LIBGUESTFS_TMPDIR=/var/lib/personal-kvm/tmp \
           LIBGUESTFS_CACHEDIR=/var/lib/personal-kvm/cache/libguestfs \
           LIBGUESTFS_BACKEND=direct \
       libguestfs-test-tool > "${INSTALL_STATE_DIR}/libguestfs-test.log" 2>&1; then
    echo "[오류] 실제 백엔드 격리 환경에서 libguestfs 검증에 실패했습니다." >&2
    tail -n 100 "${INSTALL_STATE_DIR}/libguestfs-test.log" >&2 || true
    exit 1
fi

# A newly installed system may legitimately have no OS images yet. Check an
# explicit image first, then known Rocky 10/9 names, finally any qcow2 image.
# Never create a backing-file overlay on a missing or invalid image.
DEFAULT_IMAGE="${PKVM_DEFAULT_IMAGE:-}"
if [ -n "${DEFAULT_IMAGE}" ] && [ ! -s "${DEFAULT_IMAGE}" ]; then
    echo "[오류] PKVM_DEFAULT_IMAGE 지정 파일을 찾을 수 없습니다: ${DEFAULT_IMAGE}" >&2
    exit 1
fi
if [ -z "${DEFAULT_IMAGE}" ]; then
    for candidate in \
        /data/images/Rocky-10-GenericCloud-Base.latest.x86_64.qcow2 \
        /data/images/Rocky-9-GenericCloud-Base.latest.x86_64.qcow2; do
        if [ -s "${candidate}" ]; then DEFAULT_IMAGE="${candidate}"; break; fi
    done
fi
if [ -z "${DEFAULT_IMAGE}" ]; then
    DEFAULT_IMAGE="$(find /data/images -maxdepth 1 -type f -name '*.qcow2' -size +0c -print -quit 2>/dev/null || true)"
fi
if [ -z "${DEFAULT_IMAGE}" ]; then
    echo '[주의] 검증할 OS 이미지가 아직 없습니다. 이미지 기반 virt-customize 검증을 건너뜁니다.'
else
    echo "[검증] 디스크 이미지: ${DEFAULT_IMAGE}"
    IMG_FORMAT="$(runuser -u pkvm -- qemu-img info --output=json "${DEFAULT_IMAGE}" | "${PYTHON_BIN}" -c 'import json,sys; print(json.load(sys.stdin).get("format", ""))')" \
        || { echo '[오류] 이미지 포맷 확인 실패' >&2; exit 1; }
    if [ "${IMG_FORMAT}" != qcow2 ]; then
        echo "[오류] QCOW2 형식이 아닌 이미지는 검증에 사용할 수 없습니다: ${IMG_FORMAT}" >&2
        exit 1
    fi
    VALIDATION_DIR="$(mktemp -d /data/instances/.pkvm-install-validation-XXXXXXXX)"
    chown pkvm:pkvm "${VALIDATION_DIR}"
    chmod 0700 "${VALIDATION_DIR}"
    VALIDATION_OVERLAY="${VALIDATION_DIR}/overlay.qcow2"
    if ! runuser -u pkvm -- qemu-img create -f qcow2 -F qcow2 -b "${DEFAULT_IMAGE}" "${VALIDATION_OVERLAY}" || \
       ! nsenter -t "${BACKEND_PID}" -m runuser -u pkvm -- \
           env HOME=/var/lib/personal-kvm TMPDIR=/var/lib/personal-kvm/tmp \
           LIBGUESTFS_CACHEDIR=/var/lib/personal-kvm/cache/libguestfs \
           LIBGUESTFS_TMPDIR=/var/lib/personal-kvm/tmp LIBGUESTFS_BACKEND=direct \
           /opt/personal-kvm/scripts/customize_guest.sh "${VALIDATION_OVERLAY}" pkvm-install-validation \
           > "${INSTALL_STATE_DIR}/virt-customize-test.log" 2>&1; then
        rm -rf -- "${VALIDATION_DIR}"
        echo '[오류] 이미지에 대한 virt-customize 검증 실패' >&2
        tail -n 100 "${INSTALL_STATE_DIR}/virt-customize-test.log" >&2 || true
        exit 1
    fi
    rm -rf -- "${VALIDATION_DIR}"
fi

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
Personal KVM v0.1.9
Console: https://${HOST_IP}/
Admin user: ${ADMIN_USER}
Password file: /etc/personal-kvm/.initial_admin_password
Installed: $(date --iso-8601=seconds)
EOF
chmod 0600 /etc/personal-kvm/install-summary
echo "=================================================================="
echo " [설치 완료] 퍼스널 KVM 가상화 서비스 v0.1.9 정상 구동 중!"
echo " - 관리 콘솔           : https://${HOST_IP}/"
echo " - HTTP 80/8080 요청은 HTTPS로 전환됩니다."
echo " - 백엔드 8000 포트는 127.0.0.1에서만 수신합니다."
echo " - API Swagger 문서    : https://${HOST_IP}/docs"
echo " - 초기 어드민 계정    : ${ADMIN_USER} (비밀번호는 아래 root 전용 파일에서 조회)"
echo " - 초기 비밀번호 파일   : /etc/personal-kvm/.initial_admin_password"
echo " - 설치 정보 재확인    : sudo cat /etc/personal-kvm/install-summary"
if [ "${ADMIN_PASSWORD_GENERATED}" -eq 1 ]; then
    echo " - 초기 비밀번호가 자동 생성되었습니다. 서버에서 root 권한으로 확인하십시오."
fi
INSTALL_COMPLETED=1
rm -f "${INSTALL_STATE_DIR}/failed-stage"
echo "=================================================================="
