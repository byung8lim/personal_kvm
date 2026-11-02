import os, ipaddress
from pathlib import Path
from app.core.runtime_config import runtime_config
from app.core.database import engine, SessionLocal, Base
from app.core.security import get_password_hash
from app.models.entities import AdminUser, StoragePool, VirtualNetwork, IpPool, OsImage, ResourceLock

def sync_networks_from_config(db):
    document = runtime_config()
    networks = document.get("networks") or {}
    type_map = {"bridged": "BRIDGED", "nat": "NAT", "internal": "INTERNAL", "management": "MANAGEMENT"}
    if set(networks) != set(type_map):
        raise RuntimeError("config.yaml networks에는 bridged/nat/internal/management가 모두 필요합니다.")
    all_pool_addresses = set()
    for key, net_type in type_map.items():
        item = networks[key]
        network = ipaddress.ip_network(item["cidr"], strict=True)
        start, end = ipaddress.ip_address(item["pool_start"]), ipaddress.ip_address(item["pool_end"])
        if start not in network or end not in network or int(start) > int(end):
            raise RuntimeError(f"{key} IP 풀이 CIDR 범위를 벗어났습니다.")
        gateway = item.get("gateway")
        if gateway and ipaddress.ip_address(gateway) not in network:
            raise RuntimeError(f"{key} gateway가 CIDR 범위를 벗어났습니다.")
        vn = db.query(VirtualNetwork).filter(VirtualNetwork.net_type == net_type).first()
        values = dict(net_name=item["net_name"], bridge_name=item["bridge_name"], cidr=item["cidr"],
                      gateway_ip=gateway, dns_server=item.get("dns"), ip_start=str(start), ip_end=str(end))
        if not vn:
            vn = VirtualNetwork(net_type=net_type, assigned_ips=0, **values); db.add(vn); db.flush()
        else:
            for field, value in values.items(): setattr(vn, field, value)
        desired = {str(ipaddress.ip_address(value)) for value in range(int(start), int(end) + 1)}
        if all_pool_addresses.intersection(desired):
            raise RuntimeError(f"{key} IP 풀이 다른 네트워크와 중복됩니다.")
        all_pool_addresses.update(desired)
        existing = {row.ip_address: row for row in db.query(IpPool).filter(IpPool.network_id == vn.id).all()}
        for address in desired - set(existing): db.add(IpPool(network_id=vn.id, ip_address=address, status="AVAILABLE"))
        for address, row in existing.items():
            if address not in desired and row.status == "AVAILABLE": db.delete(row)
        vn.total_ips = len(desired)
        vn.assigned_ips = db.query(IpPool).filter(IpPool.network_id == vn.id, IpPool.status == "ASSIGNED").count()
    db.commit()

def init_seed_data(db):
    Base.metadata.create_all(bind=engine)
    if not db.query(ResourceLock).filter(ResourceLock.id == 1).first():
        db.add(ResourceLock(id=1, name="host-allocation"))
        db.commit()
    u = os.getenv("PKVM_ADMIN_USER", "admin")
    existing_user = db.query(AdminUser).filter(AdminUser.username == u).first()
    if not existing_user:
        p = os.getenv("PKVM_ADMIN_PASS")
        if not p:
            raise RuntimeError("최초 관리자 생성에는 PKVM_ADMIN_PASS 환경변수가 필요합니다.")
        db.add(AdminUser(username=u, password_hash=get_password_hash(p)))
        db.commit()

    if not db.query(StoragePool).first():
        storage = runtime_config()["storage"]
        db.add_all([
            StoragePool(pool_name="root_pool", mount_path=storage["root_disk_dir"], pool_type="ROOT", total_size_gb=500, available_size_gb=500),
            StoragePool(pool_name="extra_pool", mount_path=storage["extra_disk_dir"], pool_type="EXTRA", total_size_gb=1000, available_size_gb=1000),
            StoragePool(pool_name="image_pool", mount_path=storage["image_dir"], pool_type="IMAGE", total_size_gb=200, available_size_gb=200),
        ])
        db.commit()

    sync_networks_from_config(db)

    if not db.query(OsImage).first():
        imgs = [
            ("ROCKY", "9.8", "Rocky-9-GenericCloud-Base.latest.x86_64.qcow2", "https://dl.rockylinux.org/pub/rocky/9/images/x86_64/Rocky-9-GenericCloud-Base.latest.x86_64.qcow2", 1150, "rocky"),
            ("ROCKY", "8.10", "Rocky-8-GenericCloud-Base.latest.x86_64.qcow2", "https://dl.rockylinux.org/pub/rocky/8/images/x86_64/Rocky-8-GenericCloud-Base.latest.x86_64.qcow2", 980, "rocky"),
            ("ROCKY", "10", "Rocky-10-GenericCloud-Base.latest.x86_64.qcow2", "https://dl.rockylinux.org/pub/rocky/10/images/x86_64/Rocky-10-GenericCloud-Base.latest.x86_64.qcow2", 1200, "rocky"),
            ("UBUNTU", "24.04", "noble-server-cloudimg-amd64.img", "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img", 720, "ubuntu"),
            ("UBUNTU", "22.04", "jammy-server-cloudimg-amd64.img", "https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img", 680, "ubuntu"),
        ]
        for ltype, ver, fname, url, sz, usr in imgs:
            db.add(OsImage(linux_type=ltype, version=ver, file_name=fname, file_path=str(Path(runtime_config()["storage"]["image_dir"]) / fname), source_url=url, size_mb=sz, default_user=usr, status="MISSING"))
        db.commit()

    required_images = [
        ("ROCKY", "9.8", "Rocky-9-GenericCloud-Base.latest.x86_64.qcow2", "https://dl.rockylinux.org/pub/rocky/9/images/x86_64/Rocky-9-GenericCloud-Base.latest.x86_64.qcow2", "rocky"),
        ("ROCKY", "8.10", "Rocky-8-GenericCloud-Base.latest.x86_64.qcow2", "https://dl.rockylinux.org/pub/rocky/8/images/x86_64/Rocky-8-GenericCloud-Base.latest.x86_64.qcow2", "rocky"),
        ("ROCKY", "10", "Rocky-10-GenericCloud-Base.latest.x86_64.qcow2", "https://dl.rockylinux.org/pub/rocky/10/images/x86_64/Rocky-10-GenericCloud-Base.latest.x86_64.qcow2", "rocky"),
        ("UBUNTU", "24.04", "noble-server-cloudimg-amd64.img", "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img", "ubuntu"),
        ("UBUNTU", "22.04", "jammy-server-cloudimg-amd64.img", "https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img", "ubuntu"),
    ]
    for ltype, version, filename, url, user in required_images:
        if not db.query(OsImage).filter(OsImage.linux_type == ltype, OsImage.version == version).first():
            db.add(OsImage(linux_type=ltype, version=version, file_name=filename,
                           file_path=str(Path(runtime_config()["storage"]["image_dir"]) / filename), source_url=url,
                           size_mb=0, default_user=user, status="MISSING"))
    db.commit()

if __name__ == "__main__":
    db = SessionLocal()
    init_seed_data(db)
    db.close()
