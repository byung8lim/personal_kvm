import tempfile
import unittest
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.entities import StoragePool, VirtualDisk
from app.services.storage_service import synchronize_storage_pools


class StorageServiceTests(unittest.TestCase):
    def test_summary_uses_filesystem_and_active_disk_rows(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        session = sessionmaker(bind=engine)()
        with tempfile.TemporaryDirectory() as folder:
            pool = StoragePool(id=1, pool_name="test", mount_path=folder, pool_type="EXTRA")
            session.add(pool); session.flush()
            session.add(VirtualDisk(id=1, disk_uuid="00000000-0000-0000-0000-000000000001", disk_name="a",
                                    pool_id=pool.id, disk_role="EXTRA", size_gb=20,
                                    file_path=f"{folder}/a.qcow2", status="AVAILABLE"))
            session.commit()
            rows = synchronize_storage_pools(session)
            self.assertEqual(rows[0]["disk_count"], 1)
            self.assertEqual(rows[0]["allocated_size_gb"], 20.0)
            self.assertGreater(rows[0]["total_size_gb"], 0)
        session.close()


if __name__ == "__main__": unittest.main()
