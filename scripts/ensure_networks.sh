#!/bin/bash
set -euo pipefail

URI="${LIBVIRT_DEFAULT_URI:-qemu:///system}"
for network in Public-NAT Internal-Service Management-Net; do
    if [ "$(virsh -c "${URI}" net-info "${network}" 2>/dev/null | awk '/^Active:/ {print $2}')" != "yes" ]; then
        virsh -c "${URI}" net-start "${network}"
    fi
    virsh -c "${URI}" net-autostart "${network}" >/dev/null
done

for attempt in $(seq 1 30); do
    missing=0
    for bridge in virbr0 virbr1 virbr2 virbr3; do
        ip link show dev "${bridge}" >/dev/null 2>&1 || missing=1
    done
    # virbr0 is the external bridge and must always have a physical port.
    # Merely having a bridge device is insufficient: an empty virbr0 isolates
    # bridged guests even though domain creation succeeds.
    ip -o link show master virbr0 2>/dev/null | grep -q . || missing=1
    [ "${missing}" -eq 0 ] && exit 0
    sleep 1
done

echo "[오류] 필수 가상 브리지(virbr0~virbr3) 또는 virbr0 물리 포트가 30초 안에 준비되지 않았습니다." >&2
exit 1
