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
[[ ! -L "$ROOT" ]] || { echo '[NFS 오류] NFS_DIR 심볼릭 링크는 허용하지 않습니다.' >&2; exit 2; }
getent passwd pkvm >/dev/null || { echo '[NFS 오류] pkvm 계정이 없습니다.' >&2; exit 2; }
install -d -o pkvm -g pkvm -m 0770 "$ROOT"
install -d -m 0755 /etc/exports.d
# Only explicitly configured management subnet can access this NFS export.
PKVM_UID="$(id -u pkvm)"
PKVM_GID="$(id -g pkvm)"
# Map remote identities to the dedicated service account. Never grant root on NFS.
printf '%s %s(rw,sync,all_squash,anonuid=%s,anongid=%s,no_subtree_check)\n' "$ROOT" "$CIDR" "$PKVM_UID" "$PKVM_GID" > /etc/exports.d/personal-kvm.exports
chmod 0644 /etc/exports.d/personal-kvm.exports
exportfs -ra
systemctl enable --now nfs-server
