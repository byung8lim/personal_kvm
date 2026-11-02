#!/bin/bash
set -e
IMGFILE="$1"
HOSTNAME="$2"
NM_DIR="$3"
ADMIN_USER="${4:-kvmadmin}"
SSH_PUB_KEY="${5:-/var/lib/personal-kvm/.ssh/id_rsa.pub}"
TPL_DIR="${6:-/opt/personal-kvm/templates}"

export LIBGUESTFS_BACKEND=direct
UPLOAD_ARGS=""
for nmfile in "${NM_DIR}"/*.nmconnection; do
    if [ -f "$nmfile" ]; then
        fname=$(basename "$nmfile")
        UPLOAD_ARGS="${UPLOAD_ARGS} --upload ${nmfile}:/etc/NetworkManager/system-connections/${fname}"
    fi
done

SSH_ARG=""
if [ -f "${SSH_PUB_KEY}" ]; then
    SSH_ARG="--ssh-inject ${ADMIN_USER}:file:${SSH_PUB_KEY}"
fi

/usr/bin/virt-customize -a "${IMGFILE}" \
    --hostname "${HOSTNAME}" \
    --upload "${TPL_DIR}/grub_default.tpl":/etc/default/grub \
    ${UPLOAD_ARGS} \
    --run-command "chmod 600 /etc/NetworkManager/system-connections/*.nmconnection || true" \
    --run-command "chown root:root /etc/NetworkManager/system-connections/*.nmconnection || true" \
    --run-command "grub2-mkconfig -o /boot/grub2/grub.cfg || true" \
    --run-command "id -u ${ADMIN_USER} >/dev/null 2>&1 || useradd -m -s /bin/bash ${ADMIN_USER}" \
    --run-command "echo '${ADMIN_USER} ALL=(ALL:ALL) NOPASSWD:ALL' > /etc/sudoers.d/99-${ADMIN_USER} && chmod 440 /etc/sudoers.d/99-${ADMIN_USER}" \
    ${SSH_ARG} \
    --timezone "Asia/Seoul" \
    --run-command "sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/g' /etc/ssh/sshd_config" \
    --run-command "dnf remove -y cloud-init || true" \
    --firstboot-command "xfs_growfs / || resize2fs /dev/sda1 || true" \
    --firstboot-command "nmcli connection reload && nmcli networking on || true" \
    --selinux-relabel
