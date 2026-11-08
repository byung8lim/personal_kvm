import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from app.services.bind9_ddns import DdnsError
from app.services.lb_dns import binding_for, ensure_a, remove_a_if_owned


CONFIG = {
    "dns": {"enabled": True, "forward_zone": "wbl.int", "private_domain": "wbl.int",
            "external_domain": "wbl.com"},
    "networks": {
        "bridged": {"host_ip_cidr": "192.168.0.40/24"},
        "internal": {"cidr": "10.10.60.0/24", "dns": "10.10.60.1"},
    },
}


class LbDnsTests(unittest.TestCase):
    def test_l7_private_and_external_addresses(self):
        private = binding_for({"layer":"L7","domain":"web.wbl.int"}, CONFIG)
        external = binding_for({"layer":"L7","domain":"web.wbl.com"}, CONFIG)
        self.assertEqual((private.hostname,private.address,private.zone_kind),("web","10.10.60.1","private"))
        self.assertEqual((external.address,external.zone_kind),("192.168.0.40","external"))

    def test_l4_uses_explicit_listener(self):
        item=binding_for({"layer":"L4","domain":"db.wbl.int","listen_ip":"10.10.70.1"},CONFIG)
        self.assertEqual(item.address,"10.10.70.1")

    def test_unmanaged_or_nested_names_are_rejected(self):
        for domain in ("web.example.net","nested.web.wbl.int"):
            with self.assertRaises(ValueError):
                binding_for({"layer":"L7","domain":domain},CONFIG)

    def test_dns_create_is_idempotent_and_conflicts_fail(self):
        item=binding_for({"layer":"L7","domain":"web.wbl.int"},CONFIG)
        updater=SimpleNamespace(server="127.0.0.1",add_a=Mock(),remove_a=Mock())
        self.assertTrue(ensure_a(item,updater,lookup=lambda *_: []))
        updater.add_a.assert_called_once_with("web","10.10.60.1")
        self.assertFalse(ensure_a(item,updater,lookup=lambda *_: ["web.wbl.int. 300 IN A 10.10.60.1"]))
        with self.assertRaises(DdnsError):
            ensure_a(item,updater,lookup=lambda *_: ["web.wbl.int. 300 IN A 10.10.60.99"])

    def test_delete_only_removes_owned_address(self):
        item=binding_for({"layer":"L7","domain":"web.wbl.int"},CONFIG)
        updater=SimpleNamespace(server="127.0.0.1",add_a=Mock(),remove_a=Mock())
        self.assertTrue(remove_a_if_owned(item,updater,
            lookup=lambda *_: ["web.wbl.int. 300 IN A 10.10.60.1"]))
        updater.remove_a.assert_called_once_with("web","10.10.60.1")
        with self.assertRaises(DdnsError):
            remove_a_if_owned(item,updater,lookup=lambda *_: ["web.wbl.int. 300 IN A 10.10.60.2"])


if __name__ == "__main__": unittest.main()
