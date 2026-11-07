"""NFS volume directory lifecycle. Never follow paths outside configured NFS_DIR."""
import re
from pathlib import Path

NAME = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
class VolumeError(RuntimeError): pass

def safe_root(configured):
    root=Path(configured).resolve(strict=True)
    if not root.is_dir() or root in (Path("/"),Path("/var"),Path("/srv"),Path("/etc")):
        raise VolumeError("NFS_DIR must be a dedicated existing directory")
    return root

def volume_path(configured, name):
    if not isinstance(name,str) or not NAME.fullmatch(name):
        raise VolumeError("Invalid volume name")
    root=safe_root(configured)
    target=root/name
    if target.is_symlink() or target.resolve().parent != root:
        raise VolumeError("Volume path escapes NFS_DIR")
    return target

def list_volumes(configured):
    root=safe_root(configured)
    return [{"name":p.name,"path":str(p),"bytes":sum(f.stat().st_size for f in p.rglob("*") if f.is_file() and not f.is_symlink())}
            for p in root.iterdir() if p.is_dir() and not p.is_symlink() and NAME.fullmatch(p.name)]

def create_volume(configured,name):
    target=volume_path(configured,name)
    target.mkdir(mode=0o770,exist_ok=False)
    return str(target)

def delete_volume(configured,name,mount_users):
    """Deletion requires verified empty ownership: data deletion is deliberately separate."""
    if mount_users:
        raise VolumeError("Volume is still mounted by one or more VMs")
    target=volume_path(configured,name)
    if not target.is_dir():
        raise VolumeError("Volume not found")
    if any(target.iterdir()):
        raise VolumeError("Nonempty volume requires a separately approved data purge")
    target.rmdir()
