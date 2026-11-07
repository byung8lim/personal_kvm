import unittest
from unittest.mock import Mock
from app.services.dns_inspector import validated_name, dns_status, query_record

class DnsInspectorTests(unittest.TestCase):
    def test_domain_validation(self):
        with self.assertRaises(ValueError): validated_name("bad host")
    def test_disabled(self):
        self.assertEqual(dns_status({})["status"], "disabled")
    def test_two_soa_queries(self):
        run = Mock(return_value=Mock(returncode=0, stdout="zone. 300 IN SOA ns. host. 1 2 3 4 5\n"))
        result = dns_status({"enabled": True, "server": "127.0.0.1",
                             "forward_zone": "internal.example", "reverse_zone": "60.10.10.in-addr.arpa"}, executor=run)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(run.call_count, 2)
    def test_command_not_shell(self):
        run = Mock(return_value=Mock(returncode=0, stdout=""))
        query_record("127.0.0.1", "internal.example", "SOA", executor=run)
        self.assertEqual(run.call_args.args[0][0], "dig")
        self.assertNotIn("shell", run.call_args.kwargs)
