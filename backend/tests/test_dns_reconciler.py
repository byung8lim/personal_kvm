import unittest
from types import SimpleNamespace
from unittest.mock import patch
from app.services.dns_reconciler import review, apply_missing

class ReconcilerTests(unittest.TestCase):
    def setUp(self):
        self.vm = SimpleNamespace(id=1,private_ip="10.10.60.105",dns_hostname="vm1",state="RUNNING")
        self.updater=SimpleNamespace(server="127.0.0.1",forward_zone="internal.example.",
                                     reverse_zone="60.10.10.in-addr.arpa.",
                                     add=lambda *_: None)
    @patch("app.services.dns_reconciler.query_record",return_value=[])
    def test_missing(self,_):
        self.assertEqual(review(self.updater,[self.vm])[0]["status"],"missing")
    @patch("app.services.dns_reconciler.query_record",side_effect=[
        ["vm1.internal.example. 300 IN A 10.10.60.106"], []])
    def test_conflict_is_never_overwritten(self,_):
        self.assertEqual(apply_missing(self.updater,[self.vm])[0]["status"],"conflict")
    def test_deleted_ignored(self):
        self.vm.state="DELETED"
        self.assertEqual(review(self.updater,[self.vm]),[])
