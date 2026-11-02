#!/bin/bash
set -e
IMGFILE="$1"
HOSTNAME="$2"
NETPLAN_FILE="$3"
ADMIN_USER="${4:-kvmadmin}"
SSH_PUB_KEY="${5:-/var/lib/personal-kvm/.ssh/id_rsa.pub}"
TPL_DIR="${6:-/opt/personal-kvm/templates}"

export LIBGUESTFS_BACKEND=direct
SSH_ARG=""
if [ -f "${SSH_PUB_KEY}" ]; then
    SSH_ARG="--ssh-inject ${ADMIN_USER}:file:${SSH_PUB_KEY}"
fi

/usr/bin/virt-customize -a "${IMGFILE}" \
    --hostname "${HOSTNAME}" \
    --upload "${TPL_DIR}/grub_default.tpl":/etc/default/grub \
    --upload "${NETPLAN_FILE}":/etc/netplan/01-netcfg.yaml \
    --run-command "chmod 600 /etc/netplan/01-netcfg.yaml && rm -f /etc/netplan/50-cloud-init.yaml || true" \
    --run-command "update-grub || true" \
    --run-command "id -u ${ADMIN_USER} >/dev/null 2>&1 || useradd -m -s /bin/bash ${ADMIN_USER}" \
    --run-command "echo '${ADMIN_USER} ALL=(ALL:ALL) NOPASSWD:ALL' > /etc/sudoers.d/99-${ADMIN_USER} && chmod 440 /etc/sudoers.d/99-${ADMIN_USER}" \
    ${SSH_ARG} \
    --timezone "Asia/Seoul" \
    --run-command "sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/g' /etc/ssh/sshd_config" \
    --run-command "apt-get remove -y cloud-init || true" \
    --firstboot-command "ssh-keygen -A && netplan apply || true"
