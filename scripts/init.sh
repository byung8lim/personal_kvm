#!/usr/bin/env bash
# Legacy entrypoint. One implementation path for Rocky Linux 9.x and 10.x.
# The former script disabled firewalld/SELinux and overwrote hostname;
# those actions are unsafe for managed KVM servers and are no longer performed.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "${EUID}" -ne 0 ]; then
    echo "[오류] install.sh는 반드시 root 권한(sudo)으로 실행해야 합니다."
    exit 1
fi

BASE_DIR=$(dirname $(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd))
TEMPLATE_DIR="$BASE_DIR/templates"

tee /etc/hostname<<EOF
minipc
EOF

cp $TEMPLATE_DIR/admin /etc/sudoers.d/

setenforce 0
cp $TEMPLATE_DIR/selinux /etc/sysconfig/

systemctl disable firewalld

hostname -F /etc/hostname

FIRST_NIC=$(ls -1 /sys/class/net | grep -v '^lo$' | head -n 1 || true)
if [ -z "${FIRST_NIC}" ]; then
    echo "오류: 활성화된 네트워크 인터페이스를 찾을 수 없습니다." >&2
    exit 1
fi

NIC_IP=$(ip -4 -brief addr show dev "${FIRST_NIC}" 2>/dev/null | awk '{print $3}' | cut -d/ -f1)
if [ -z "${NIC_IP}" ]; then
    echo "오류: 활성화된 IP 찾을 수 없습니다." >&2
    exit 1
fi

echo "" >> /etc/hosts
echo "${NIC_IP} minipc" >> /etc/hosts

dnf update -y

dnf -y install epel-release
dnf -y install net-tools bind-utils telnet wget curl tar zip unzip hostname chrony

systemctl enable --now chronyd
systemctl enable --now NetworkManager

exec "${SCRIPT_DIR}/install.sh" "$@"
