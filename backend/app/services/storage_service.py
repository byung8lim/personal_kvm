import shutil
from pathlib import Path
from typing import Dict, List

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.entities import StoragePool, VirtualDisk


def synchronize_storage_pools(db: Session) -> List[Dict[str, object]]:
    result = []
    for pool in db.query(StoragePool).with_for_update().all():
        path = Path(pool.mount_path)
        path.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(path)
        total_gb = round(usage.total / (1024 ** 3), 2)
        physical_free_gb = round(usage.free / (1024 ** 3), 2)
        allocated_gb, disk_count = db.query(
            func.coalesce(func.sum(VirtualDisk.size_gb), 0), func.count(VirtualDisk.id)
        ).filter(VirtualDisk.pool_id == pool.id, VirtualDisk.status != "DELETED").one()
        allocated_gb = float(allocated_gb or 0)
        pool.total_size_gb = total_gb
        pool.allocated_size_gb = allocated_gb
        pool.available_size_gb = max(0, min(physical_free_gb, total_gb - allocated_gb))
        result.append({"id": pool.id, "pool_name": pool.pool_name, "mount_path": pool.mount_path,
                       "pool_type": pool.pool_type, "total_size_gb": total_gb,
                       "allocated_size_gb": allocated_gb, "available_size_gb": float(pool.available_size_gb),
                       "physical_free_gb": physical_free_gb, "disk_count": int(disk_count)})
    db.commit()
    return result
