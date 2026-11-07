import unittest
from app.services.haproxy_l4 import render_tcp_service

SPEC = {"name": "db", "listen_ip": "10.10.70.1", "listen_port": 5432,
        "algorithm": "leastconn",
        "backends": [{"host": "db01.internal.example", "port": 5432},
                     {"host": "db02.internal.example", "port": 5432, "standby": True}]}

class HaproxyTests(unittest.TestCase):
    def test_backup_and_algorithm(self):
        output = render_tcp_service(SPEC)
        self.assertIn("balance leastconn", output)
        self.assertIn("backup", output)
        self.assertIn("bind 10.10.70.1:5432", output)
    def test_invalid_service_name(self):
        with self.assertRaises(ValueError):
            render_tcp_service({**SPEC,"name":"db\nuserlist admin"})
    def test_no_wildcard_listener(self):
        with self.assertRaises(ValueError):
            render_tcp_service({**SPEC,"listen_ip":"0.0.0.0"})
    def test_no_invalid_port(self):
        with self.assertRaises(ValueError):
            render_tcp_service({**SPEC,"listen_port":65536})
