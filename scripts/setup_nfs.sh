#!/usr/bin/env bash
set -euo pipefail
[[ $EUID -eq 0 && $# -eq 2 ]] || { echo 'Usage: sudo setup_nfs.sh <NFS_DIR> <management-CIDR>' >&2; exit 2; }
ROOT="$1"; CIDR="$2"
python3 - "$ROOT" "$CIDR" <<'PY'
import ipaddress,sys
from pathlib import Path
p=Path(sys.argv[1]); network=ipaddress.ip_network(sys.argv[2],strict=True)
if not p.is_absolute() or '..' in p.parts or str(p) in ('/','/var','/srv','/etc'):
    sys.exit("Dedicated NFS_DIR required")
if network.version!=4 or network.prefixlen<16: sys.exit("Invalid private management network")
if not network.is_private: sys.exit("NFS must not be exported to public networks")
PY
rpm -q nfs-utils >/dev/null 2>&1 || dnf install -y nfs-utils
install -d -o root -g root -m 0750 "$ROOT"
install -d -m 0755 /etc/exports.d
# Only explicitly configured management subnet can access this NFS export.
printf '%s %s(rw,sync,root_squash,no_subtree_check)\n' "$ROOT" "$CIDR" > /etc/exports.d/personal-kvm.exports
chmod 0644 /etc/exports.d/personal-kvm.exports
exportfs -ra
systemctl enable --now nfs-server
