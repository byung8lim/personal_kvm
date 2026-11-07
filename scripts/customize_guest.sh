#!/bin/bash
set -euo pipefail
IMAGE_PATH="$1"
HOST_NAME="$2"
TEMPLATE_DIR="${3:-/opt/personal-kvm/templates}"
test -f "${IMAGE_PATH}"
test -f "${TEMPLATE_DIR}/grub_default.tpl"
# Prefer the service-owned state directory, but support direct operator invocation.
# Never silently fall back to a read-only /tmp inside a systemd sandbox.
export LIBGUESTFS_BACKEND=direct
export TMPDIR="${TMPDIR:-/var/lib/personal-kvm/tmp}"
export LIBGUESTFS_TMPDIR="${LIBGUESTFS_TMPDIR:-${TMPDIR}}"
export LIBGUESTFS_CACHEDIR="${LIBGUESTFS_CACHEDIR:-/var/lib/personal-kvm/cache/libguestfs}"
for guestfs_dir in "$TMPDIR" "$LIBGUESTFS_TMPDIR" "$LIBGUESTFS_CACHEDIR"; do
    if [ ! -d "$guestfs_dir" ] || [ ! -w "$guestfs_dir" ] || [ ! -x "$guestfs_dir" ]; then
        echo "[오류] libguestfs 임시/캐시 디렉터리를 사용할 수 없습니다: $guestfs_dir" >&2
        exit 1
    fi
done
/usr/bin/virt-customize -a "${IMAGE_PATH}" \
    --hostname "${HOST_NAME}" \
    --upload "${TEMPLATE_DIR}/grub_default.tpl:/etc/default/grub" \
    --timezone "Asia/Seoul" \
    --run-command "ssh-keygen -A || true"
