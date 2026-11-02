#!/bin/bash
set -euo pipefail
IMAGE_PATH="$1"
HOST_NAME="$2"
TEMPLATE_DIR="${3:-/opt/personal-kvm/templates}"
test -f "${IMAGE_PATH}"
test -f "${TEMPLATE_DIR}/grub_default.tpl"
export LIBGUESTFS_BACKEND=direct
/usr/bin/virt-customize -a "${IMAGE_PATH}" \
    --hostname "${HOST_NAME}" \
    --upload "${TEMPLATE_DIR}/grub_default.tpl:/etc/default/grub" \
    --timezone "Asia/Seoul" \
    --run-command "ssh-keygen -A || true"
