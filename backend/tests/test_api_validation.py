import os
import unittest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret")

from pydantic import ValidationError
from app.schemas.requests import ChangePasswordReq, CreateDiskReq, CreateInstanceReq, ResizeInstanceReq


class ApiValidationTests(unittest.TestCase):
    def test_instance_resource_ranges(self):
        with self.assertRaises(ValidationError):
            CreateInstanceReq(name="vm", os_image_id=1, vcores=5)
        with self.assertRaises(ValidationError):
            CreateInstanceReq(name="vm", os_image_id=1, memory_gb=9)
        with self.assertRaises(ValidationError):
            CreateInstanceReq(name="vm", os_image_id=1, root_disk_gb=9)
        with self.assertRaises(ValidationError):
            CreateInstanceReq(name="vm", os_image_id=1, extra_disk_gb=5)

    def test_network_selection_rule(self):
        with self.assertRaises(ValidationError):
            CreateInstanceReq(name="vm", os_image_id=1, public_net_type=None, internal_network=False)

    def test_disk_and_resize_ranges(self):
        with self.assertRaises(ValidationError): CreateDiskReq(disk_name="bad/name", size_gb=20)
        with self.assertRaises(ValidationError): ResizeInstanceReq(vcores=0, memory_gb=4)

    def test_password_complexity(self):
        with self.assertRaises(ValidationError): ChangePasswordReq(current_password="old", new_password="onlylowercase")
        ChangePasswordReq(current_password="old", new_password="ValidPass123!")


if __name__ == "__main__": unittest.main()
