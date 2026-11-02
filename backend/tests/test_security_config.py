import os
import unittest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret")

from app.core.config import parse_allowed_origins


class CorsConfigurationTests(unittest.TestCase):
    def test_empty_allowlist_keeps_same_origin_default(self):
        self.assertEqual(parse_allowed_origins(""), [])

    def test_comma_separated_origins_are_normalized(self):
        self.assertEqual(
            parse_allowed_origins(" https://kvm.example.test/,http://10.0.0.10:8080 "),
            ["https://kvm.example.test", "http://10.0.0.10:8080"],
        )

    def test_wildcard_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_allowed_origins("*")


if __name__ == "__main__":
    unittest.main()
