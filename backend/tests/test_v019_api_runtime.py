"""The authenticated API must expose real runtime evidence, not DB guesses."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.core.database import get_db
from app.core.security import require_active_admin


class _Query:
    def __init__(self, item):
        self.item = item

    def filter(self, *_):
        return self

    def first(self):
        return self.item


class RuntimeApiRegression(unittest.TestCase):
    def setUp(self):
        self.row = SimpleNamespace(id=7, name="bank", layer="L4", status="ACTIVE")
        app.dependency_overrides[get_db] = lambda: SimpleNamespace(query=lambda *_: _Query(self.row))
        app.dependency_overrides[require_active_admin] = lambda: "test_admin"
        self.client = TestClient(app)
        self.addCleanup(app.dependency_overrides.clear)

    def test_l4_health_uses_privileged_live_agent(self):
        expected = {"state": "ACTIVE_STANDBY", "primary_reachable": True,
                    "backends": {"node1": {"status": "MAINT", "current_sessions": 0},
                                 "node2": {"status": "UP", "current_sessions": 2}},
                    "failback_available": True}
        with patch("app.main.lb_agent_request", return_value=expected) as agent:
            result = self.client.get("/api/v1/load-balancers/7/health")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["runtime_status"], "observed")
        self.assertEqual(result.json()["backends"]["node2"]["current_sessions"], 2)
        agent.assert_called_once_with("health_l4", name="bank")

    def test_l4_agent_failure_is_503_not_false_healthy(self):
        with patch("app.main.lb_agent_request", side_effect=RuntimeError("socket unavailable")):
            result = self.client.get("/api/v1/load-balancers/7/health")
        self.assertEqual(result.status_code, 503)

    def test_non_l4_never_claims_live_health(self):
        self.row.layer = "L7"
        result = self.client.get("/api/v1/load-balancers/7/health")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["runtime_status"], "unavailable")
        self.assertFalse(result.json()["failback_available"])


if __name__ == "__main__":
    unittest.main()
