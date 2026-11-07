"""Static integration guard for supported MySQL IP allocation modes.

A full acceptance test must also use an actual MySQL/MariaDB instance.
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]

class AllocationContractTests(unittest.TestCase):
    def test_numeric_serial_and_random_queries_present(self):
        source = (ROOT / "backend/app/main.py").read_text(encoding="utf-8")
        self.assertIn('func.rand()', source)
        self.assertIn('func.inet_aton(IpPool.ip_address)', source)
        self.assertIn('with_for_update(skip_locked=True)', source)

    def test_random_default(self):
        source = (ROOT / "config/config.yaml").read_text(encoding="utf-8")
        self.assertIn('ip_allocation_strategy: "RANDOM"', source)

if __name__ == "__main__":
    unittest.main()
