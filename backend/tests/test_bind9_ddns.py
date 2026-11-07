import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.bind9_ddns import Bind9Ddns, DdnsError


class Bind9DdnsTests(unittest.TestCase):
    def test_add_forward_and_reverse(self):
        with tempfile.TemporaryDirectory() as root:
            key = Path(root) / "key"; key.write_text("test")
            dns = Bind9Ddns("127.0.0.1", str(key), "internal.example", "60.10.10.in-addr.arpa")
            with patch("app.services.bind9_ddns.subprocess.run") as run:
                run.return_value.returncode = 0
                dns.add("vm-one", "10.10.60.101")
                self.assertEqual(run.call_count, 2)
                self.assertIn("vm-one.internal.example.", run.call_args_list[0].kwargs["input"])
                self.assertIn("101.60.10.10.in-addr.arpa.", run.call_args_list[1].kwargs["input"])

    def test_rejects_wrong_reverse_zone_before_update(self):
        dns = Bind9Ddns("127.0.0.1", "/missing/key", "internal.example", "70.10.10.in-addr.arpa")
        with self.assertRaises(ValueError):
            dns.add("vm", "10.10.60.101")

    def test_no_secret_or_stderr_leaked(self):
        with tempfile.TemporaryDirectory() as root:
            key = Path(root) / "key"; key.write_text("secret")
            dns = Bind9Ddns("127.0.0.1", str(key), "internal.example", "60.10.10.in-addr.arpa")
            with patch("app.services.bind9_ddns.subprocess.run") as run:
                run.return_value.returncode = 1
                run.return_value.stderr = "secret data"
                with self.assertRaises(DdnsError) as failure:
                    dns.add("vm", "10.10.60.101")
                self.assertNotIn("secret", str(failure.exception))


if __name__ == "__main__":
    unittest.main()
