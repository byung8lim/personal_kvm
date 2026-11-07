"""Deployment path regressions without touching host services."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("traefik_publish_under_test", ROOT / "scripts" / "apply_traefik_route.py")
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)


class DeploymentRegression(unittest.TestCase):
    def test_invalid_route_preserves_last_good_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            dest = directory / "svc.yaml"
            dest.write_text("previous: valid\n")
            invalid = {"name": "svc", "domain": "invalid..name",
                       "backends": [{"url": "http://127.0.0.1:8080"}]}
            with self.assertRaises(ValueError):
                publisher.publish(invalid, directory, verify_binary=False)
            self.assertEqual(dest.read_text(), "previous: valid\n")

    def test_apply_refuses_old_traefik_binary(self):
        valid = {"name": "svc", "domain": "web.lab.local",
                 "backends": [{"url": "http://127.0.0.1:8080"}]}
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(publisher, "verify_traefik_version", side_effect=RuntimeError("old")):
            with self.assertRaises(RuntimeError):
                publisher.publish(valid, Path(tmp))
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_install_registers_agent_and_never_prints_password(self):
        script = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
        self.assertIn("systemctl restart pkvm-lb-agent.service", script)
        self.assertIn("[ ! -S /run/pkvm-lb-agent/control.sock ]", script)
        self.assertNotIn("${ADMIN_USER} / ${ADMIN_PASS}", script)


if __name__ == "__main__":
    unittest.main()
