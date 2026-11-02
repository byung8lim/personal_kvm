"""Opt-in integration smoke tests for a dedicated Rocky KVM runner."""
import os
import shutil
import subprocess
import unittest

from sqlalchemy import create_engine, text


@unittest.skipUnless(os.getenv("PKVM_RUN_LIVE_INTEGRATION") == "1", "set PKVM_RUN_LIVE_INTEGRATION=1 on an isolated KVM host")
class LiveDependencyTests(unittest.TestCase):
    def test_mysql_transaction_round_trip(self):
        engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
        with engine.begin() as connection:
            self.assertEqual(connection.execute(text("SELECT 1")).scalar_one(), 1)

    def test_libvirt_qemu_and_network_manager_are_live(self):
        for command in ("virsh", "qemu-img", "nmcli"):
            self.assertIsNotNone(shutil.which(command), command)
        checks = (["virsh", "-c", "qemu:///system", "uri"], ["qemu-img", "--version"], ["nmcli", "general", "status"])
        for command in checks:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
