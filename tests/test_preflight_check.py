"""Hardware checks with synthetic host facts; never modify the running host."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "preflight_check.sh"
V3_FLAGS = "vmx fpu cx8 cmov fxsr mmx sse sse2 pni ssse3 sse4_1 sse4_2 popcnt cx16 lahf_lm avx avx2 bmi1 bmi2 fma movbe f16c xsave osxsave abm"


class PreflightTests(unittest.TestCase):
    def check_host(self, version, flags, os_id="rocky"):
        with tempfile.TemporaryDirectory() as temp:
            os_file = Path(temp) / "os-release"
            cpu_file = Path(temp) / "cpuinfo"
            os_file.write_text(f'ID={os_id}\nVERSION_ID="{version}"\n', encoding="utf-8")
            cpu_file.write_text(f'processor : 0\nflags : {flags}\n', encoding="utf-8")
            env = {**os.environ, "PKVM_OS_RELEASE_FILE": str(os_file),
                   "PKVM_CPUINFO_FILE": str(cpu_file)}
            return subprocess.run(["bash", str(SCRIPT)], env=env, text=True,
                                  capture_output=True, check=False)

    def test_rocky9_requires_virtualization(self):
        self.assertEqual(self.check_host("9.8", "svm sse2").returncode, 0)
        self.assertNotEqual(self.check_host("9.8", "sse2").returncode, 0)

    def test_rocky10_requires_full_v3(self):
        self.assertEqual(self.check_host("10.2", V3_FLAGS).returncode, 0)
        result = self.check_host("10.2", V3_FLAGS.replace(" avx2", ""))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("avx2", result.stderr)

    def test_unsupported_os_rejected(self):
        self.assertNotEqual(self.check_host("10.2", V3_FLAGS, "fedora").returncode, 0)


if __name__ == "__main__":
    unittest.main()
