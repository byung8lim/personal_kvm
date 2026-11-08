"""L7 configuration and DNS live-state regression tests (stdlib runner)."""
from types import SimpleNamespace
import unittest

from app.services.dns_inspector import inspect_owned_pair
from app.services.traefik_l7 import render_route, verify_traefik_version
from app.services.haproxy_l4 import render_tcp_service


class DnsRecordRegression(unittest.TestCase):
    def test_matches_actual_a_and_ptr(self):
        def lookup(_server, name, record_type):
            return (["vm.lab.local. 300 IN A 10.10.50.12"] if record_type == "A" else
                    ["12.50.10.10.in-addr.arpa. 300 IN PTR vm.lab.local."])
        result = inspect_owned_pair("127.0.0.1", "vm", "10.10.50.12",
                                    "lab.local", "50.10.10.in-addr.arpa", lookup=lookup)
        self.assertEqual(result["status"], "ok")

    def test_rejects_conflicting_live_a(self):
        def lookup(_server, name, record_type):
            return (["vm.lab.local. 300 IN A 10.10.50.99"] if record_type == "A" else [])
        result = inspect_owned_pair("127.0.0.1", "vm", "10.10.50.12",
                                    "lab.local", "50.10.10.in-addr.arpa", lookup=lookup)
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(result["actual_a"], ["10.10.50.99"])

    def test_dns_error_does_not_claim_match(self):
        def lookup(*_):
            raise RuntimeError("DNS unavailable")
        result = inspect_owned_pair("127.0.0.1", "vm", "10.10.50.12",
                                    "lab.local", "50.10.10.in-addr.arpa", lookup=lookup)
        self.assertEqual(result["status"], "query_failed")


class TraefikRegression(unittest.TestCase):
    SPEC = {"name": "svc", "domain": "web.lab.local",
            "backends": [{"url": "http://10.10.50.12:8080", "weight": 2}],
            "healthcheck_path": "/health"}

    def test_4_strategies_and_healthcheck_schema(self):
        for name in ("wrr", "p2c", "hrw", "leasttime"):
            with self.subTest(name=name):
                cfg = render_route({**self.SPEC, "algorithm": name})
                lb = cfg["http"]["services"]["svc"]["loadBalancer"]
                self.assertEqual(lb["strategy"] if name != "wrr" else "wrr", name)
                self.assertEqual(lb["healthcheck"]["path"], "/health")
                self.assertEqual(lb["servers"][0]["weight"], 2)

    def test_rejects_invalid_healthcheck_duration(self):
        with self.assertRaises(ValueError):
            render_route({**self.SPEC, "healthcheck_interval": "5s\napi: true"})

    def test_requires_traefik_at_least_3_4(self):
        executor = lambda *a, **kw: SimpleNamespace(returncode=0, stdout="Version: 3.4.2\n")
        self.assertEqual(verify_traefik_version(executor=executor), (3, 4))
        old = lambda *a, **kw: SimpleNamespace(returncode=0, stdout="Version: 3.3.1\n")
        with self.assertRaisesRegex(RuntimeError, ">= 3.4"):
            verify_traefik_version(executor=old)


class HaproxyThresholdRegression(unittest.TestCase):
    def test_proxy_fall_uses_same_threshold_as_monitor(self):
        config = render_tcp_service({"name": "bank", "domain": "bank.wbl.int", "listen_ip": "10.10.50.1",
                                     "listen_port": 5432, "failover_threshold": 2,
                                     "backends": [{"host": "10.10.50.11", "port": 5432},
                                                  {"host": "10.10.50.12", "port": 5432,
                                                   "standby": True}]})
        self.assertIn("fall 2 rise 2", config)


if __name__ == "__main__":
    unittest.main()
