#!/usr/bin/env bash
# v0.0.7: Run on the KVM HOST to validate host -> every VM NIC.
set -euo pipefail
VM="${1:?Usage: sudo scripts/verify_vm_connectivity.sh VM_NAME [ip ...]}"
shift
virsh -c qemu:///system dominfo "$VM"
virsh -c qemu:///system domiflist "$VM"
if [ "$#" -eq 0 ]; then
  echo 'Provide every assigned IP as additional arguments (DB allocations are not guest confirmations).' >&2
  exit 2
fi
FAILED=0
for IP in "$@"; do
  echo "==== $IP ===="
  ip route get "$IP" || FAILED=1
  if ping -c 2 -W 2 "$IP"; then
    echo "ICMP OK: $IP"
  else
    echo "ICMP FAILED: $IP (check cloud-init, VM firewall, bridge, and guest IP)" >&2
    FAILED=1
  fi
  if timeout 3 bash -c 'cat < /dev/null > /dev/tcp/"$1"/22' _ "$IP" 2>/dev/null; then
    echo "TCP/22 OPEN: $IP"
  else
    echo "TCP/22 not reachable: $IP (check sshd and guest firewall)" >&2
    FAILED=1
  fi
done
exit "$FAILED"
