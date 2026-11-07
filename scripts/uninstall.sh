#!/bin/bash
set -e
if [ "${EUID}" -ne 0 ]; then
    echo "[오류] uninstall.sh는 반드시 root 권한(sudo)으로 실행해야 합니다."
    exit 1
fi
export LIBVIRT_DEFAULT_URI="qemu:///system"

if command -v mariadb >/dev/null 2>&1 || command -v mysql >/dev/null 2>&1; then
    DB_CHECK_CLI=$(command -v mariadb || command -v mysql)
    DB_ACTIVE_VMS=$("${DB_CHECK_CLI}" -N -s -u root -e "SELECT name FROM personal_kvm.instances WHERE state IN ('RUNNING','STARTING','CREATING','STOPPING','RESIZING');" 2>/dev/null || true)
    if [ -n "${DB_ACTIVE_VMS}" ]; then
        echo "[경고] DB에 활성 상태인 가상 인스턴스가 존재합니다: ${DB_ACTIVE_VMS}"
        exit 1
    fi
fi

INSTALL_STATE_DIR="/var/lib/personal-kvm/install-state"

if command -v virsh >/dev/null 2>&1; then
    RUNNING_VMS=$(virsh -c qemu:///system list --name 2>/dev/null | sed '/^$/d' || true)
    if [ -n "${RUNNING_VMS}" ]; then
        echo "[경고] 동작 중인 가상 인스턴스가 존재합니다: ${RUNNING_VMS}"
        exit 1
    fi
    STOPPED_VMS=$(virsh -c qemu:///system list --all --name 2>/dev/null | sed '/^$/d' || true)
    for vm in ${STOPPED_VMS}; do
        while IFS= read -r disk_path; do
            case "${disk_path}" in
                /data/instances/*|/data2/disks/*) rm -f -- "${disk_path}" ;;
                ""|-) ;;
                *) echo "[경고] 관리 경로 외부 디스크는 보존합니다: ${disk_path}" ;;
            esac
        done < <(virsh -c qemu:///system domblklist "${vm}" --details 2>/dev/null | awk '$1=="file" {print $4}')
        virsh -c qemu:///system undefine "${vm}" --nvram 2>/dev/null || virsh -c qemu:///system undefine "${vm}" 2>/dev/null || true
    done
    for net in Public-NAT Internal-Service Management-Net; do
        virsh -c qemu:///system net-destroy "${net}" 2>/dev/null || true
        virsh -c qemu:///system net-undefine "${net}" 2>/dev/null || true
    done
fi

# 도메인이 해제된 뒤 관리 스토리지의 물리 파일을 먼저 회수한다.
rm -rf /data/instances/* /data2/disks/* /data/images/*

DB_UNIT=''
DB_CLI=''
DB_DUMP=''
if systemctl is-active --quiet mariadb.service; then DB_UNIT=mariadb.service
elif systemctl is-active --quiet mysqld.service; then DB_UNIT=mysqld.service; fi
if command -v mariadb >/dev/null 2>&1; then DB_CLI=mariadb
elif command -v mysql >/dev/null 2>&1; then DB_CLI=mysql; fi
if command -v mariadb-dump >/dev/null 2>&1; then DB_DUMP=mariadb-dump
elif command -v mysqldump >/dev/null 2>&1; then DB_DUMP=mysqldump; fi
if [ -n "$DB_UNIT" ] && [ -n "$DB_CLI" ] && [ -n "$DB_DUMP" ]; then
    AUDIT_DIR="/var/lib/personal-kvm/uninstall-audit"
    mkdir -p "${AUDIT_DIR}"
    AUDIT_FILE="${AUDIT_DIR}/personal_kvm_$(date +%Y%m%d_%H%M%S).sql"
    "${DB_CLI}" -u root <<MYSQL_EOF || true
USE personal_kvm;
UPDATE instances SET state='DELETED', public_ip=NULL, private_ip=NULL, mgmt_ip=NULL WHERE state <> 'DELETED';
UPDATE virtual_disks SET status='DELETED', is_assigned=0 WHERE status <> 'DELETED';
UPDATE ip_pools SET status='AVAILABLE', instance_id=NULL, assigned_at=NULL WHERE status='ASSIGNED';
UPDATE virtual_networks SET assigned_ips=0;
UPDATE resource_operations SET status='FAILED', error_message='service uninstalled before operation completion', completed_at=NOW() WHERE status IN ('PENDING','RUNNING');
MYSQL_EOF
    "${DB_DUMP}" -u root --single-transaction personal_kvm > "${AUDIT_FILE}"
    chmod 0600 "${AUDIT_FILE}"
    "${DB_CLI}" -u root <<MYSQL_EOF
DROP DATABASE IF EXISTS personal_kvm;
DROP USER IF EXISTS 'pkvm_user'@'127.0.0.1';
DROP USER IF EXISTS 'pkvm_user'@'localhost';
MYSQL_EOF
fi

systemctl stop pkvm-backend.service 2>/dev/null || true
systemctl disable pkvm-backend.service 2>/dev/null || true
systemctl disable --now pkvm-lb-agent.service 2>/dev/null || true
systemctl disable --now pkvm-ha-monitor.service 2>/dev/null || true
systemctl disable --now pkvm-haproxy.service 2>/dev/null || true
systemctl disable --now pkvm-traefik.service 2>/dev/null || true
systemctl disable --now pkvm-tls-renew.timer 2>/dev/null || true
rm -f /etc/systemd/system/pkvm-backend.service /etc/systemd/system/pkvm-tls-renew.service \
    /etc/systemd/system/pkvm-tls-renew.timer /etc/systemd/system/pkvm-lb-agent.service \
    /etc/systemd/system/pkvm-ha-monitor.service /etc/systemd/system/pkvm-haproxy.service \
    /etc/systemd/system/pkvm-traefik.service /etc/nginx/conf.d/personal-kvm.conf
rm -f /etc/sysctl.d/99-kvm-net.conf /etc/qemu/bridge.conf /etc/qemu-kvm/bridge.conf
if [ -f "${INSTALL_STATE_DIR}/nginx.conf.before" ]; then
    cp -a "${INSTALL_STATE_DIR}/nginx.conf.before" /etc/nginx/nginx.conf
fi
if [ -d "${INSTALL_STATE_DIR}/nm-connections.before" ]; then
    rm -rf /etc/NetworkManager/system-connections
    cp -a "${INSTALL_STATE_DIR}/nm-connections.before" /etc/NetworkManager/system-connections
    nmcli connection reload 2>/dev/null || true
fi
systemctl daemon-reload || true
systemctl restart nginx 2>/dev/null || true

rm -rf /opt/personal-kvm /etc/personal-kvm
if [ -f "${INSTALL_STATE_DIR}/ssh-key-created" ]; then
    rm -f /var/lib/personal-kvm/.ssh/id_rsa /var/lib/personal-kvm/.ssh/id_rsa.pub
fi
userdel -r pkvm 2>/dev/null || true
groupdel pkvm 2>/dev/null || true
if [ -s "${INSTALL_STATE_DIR}/packages.added" ]; then
    xargs -r dnf remove -y < "${INSTALL_STATE_DIR}/packages.added" || true
fi
rm -rf "${INSTALL_STATE_DIR}"
echo "[완료] 퍼스널 KVM 가상화 서비스 초기화가 완료되었습니다."
