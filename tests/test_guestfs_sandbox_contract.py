import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]

class GuestfsSandboxRegression(unittest.TestCase):
    def test_service_writable_temporary_directories(self):
        unit = (ROOT / "config/systemd/pkvm-backend.service").read_text()
        self.assertIn("ProtectSystem=strict", unit)
        self.assertIn("PrivateTmp=true", unit)
        rw = next(line for line in unit.splitlines() if line.startswith("ReadWritePaths="))
        self.assertIn(" /tmp /var/tmp ", rw)
        for key in ("TMPDIR", "LIBGUESTFS_TMPDIR", "LIBGUESTFS_CACHEDIR"):
            self.assertIn("Environment=" + key + "=", unit)

    def test_virt_customize_entrypoints_validate_writable_paths(self):
        for name in ("customize_guest.sh", "customize_rocky.sh", "customize_ubuntu.sh"):
            script = ROOT / "scripts" / name
            with self.subTest(name=name):
                self.assertIn('LIBGUESTFS_TMPDIR', script.read_text())
                self.assertIn('LIBGUESTFS_CACHEDIR', script.read_text())
                self.assertIn('! -w "$guestfs_dir"', script.read_text())
                self.assertEqual(subprocess.run(["bash", "-n", str(script)], capture_output=True).returncode, 0)

    def test_install_and_acceptance_check_real_mount_namespace(self):
        for name in ("install.sh", "acceptance.sh"):
            script = ROOT / "scripts" / name
            with self.subTest(name=name):
                body = script.read_text()
                self.assertIn("nsenter -t", body)
                self.assertIn("/tmp /var/tmp", body)
                self.assertIn("mktemp -d", body)
                self.assertEqual(subprocess.run(["bash", "-n", str(script)], capture_output=True).returncode, 0)

if __name__ == "__main__":
    unittest.main()
