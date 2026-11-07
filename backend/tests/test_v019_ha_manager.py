"""v0.1.9 safety regressions for real HA state-machine transitions.

Runs with Python unittest; no KVM or HAProxy daemon is required.
"""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


PATH = Path(__file__).resolve().parents[2] / "scripts" / "ha_manager.py"
SPEC = importlib.util.spec_from_file_location("ha_manager_under_test", PATH)
ha = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ha)


def stats(primary="MAINT", sessions=0):
    return {"node1": {"status": primary, "current_sessions": 0},
            "node2": {"status": "UP", "current_sessions": sessions}}


class HaManagerRegression(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.specs = [{"name": "bank", "failover_threshold": 2,
                       "backends": [{"host": "127.0.0.1", "port": 7001},
                                    {"host": "127.0.0.2", "port": 7002, "standby": True}]}]
        directory = Path(self.tmp.name)
        (directory / "ha-services.json").write_text(json.dumps(self.specs))
        patches = [patch.object(ha, "STATE", directory / "state.json"),
                   patch.object(ha, "LOCK", directory / "state.lock"),
                   patch.object(ha, "SPECS", directory / "ha-services.json")]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def test_threshold_requires_two_consecutive_failures(self):
        with patch.object(ha, "reachable", side_effect=[False, True, False, False]), \
             patch.object(ha, "command") as command:
            for _ in range(4):
                ha.apply_once(self.specs)
        self.assertEqual(ha.load()["bank"], "ACTIVE_STANDBY")
        self.assertEqual(ha.load()["bank_failures"], 2)
        command.assert_called_once_with("disable server pkvm_bank_nodes/node1")

    def test_no_automatic_failback_after_primary_recovers(self):
        ha.save({"bank": "ACTIVE_STANDBY", "bank_failures": 2})
        with patch.object(ha, "reachable", return_value=True), \
             patch.object(ha, "command") as command:
            ha.apply_once(self.specs)
        self.assertEqual(ha.load()["bank"], "ACTIVE_STANDBY")
        command.assert_called_once_with("disable server pkvm_bank_nodes/node1")

    def test_failback_waits_for_existing_sessions(self):
        ha.save({"bank": "ACTIVE_STANDBY"})
        with patch.object(ha, "reachable", return_value=True), \
             patch.object(ha, "stats_for", side_effect=[stats(), stats("UP"),
                                                      stats("UP", 3), stats("UP", 0)]), \
             patch.object(ha, "command") as command, \
             patch.object(ha.time, "sleep") as pause:
            ha.failback("bank")
        self.assertEqual(ha.load()["bank"], "ACTIVE_PRIMARY")
        self.assertEqual(pause.call_count, 1)
        self.assertEqual([call.args[0] for call in command.call_args_list], [
            "enable server pkvm_bank_nodes/node1",
            "set server pkvm_bank_nodes/node2 state drain",
            "set server pkvm_bank_nodes/node2 state ready"])

    def test_failback_timeout_restores_standby(self):
        ha.save({"bank": "ACTIVE_STANDBY"})
        with patch.object(ha, "reachable", return_value=True), \
             patch.object(ha, "stats_for", side_effect=lambda _: stats("UP", 5)), \
             patch.object(ha, "command") as command:
            with self.assertRaises(TimeoutError):
                ha.failback("bank", drain_timeout=0.001, poll_interval=0.01)
        self.assertEqual(ha.load()["bank"], "ACTIVE_STANDBY")
        self.assertIn("disable server pkvm_bank_nodes/node1",
                      [c.args[0] for c in command.call_args_list])

    def test_runtime_health_observes_ha_proxy_counters(self):
        ha.save({"bank": "ACTIVE_STANDBY"})
        with patch.object(ha, "reachable", return_value=True), \
             patch.object(ha, "stats_for", return_value=stats("MAINT", 2)):
            result = ha.health("bank")
        self.assertTrue(result["failback_available"])
        self.assertEqual(result["backends"]["node2"]["current_sessions"], 2)

    def test_stats_csv_parsing(self):
        output = ("# pxname,svname,scur,status\n"
                  "pkvm_bank_nodes,node1,0,MAINT\n"
                  "pkvm_bank_nodes,node2,3,UP\n")
        with patch.object(ha, "command", return_value=output):
            result = ha.stats_for("bank")
        self.assertEqual(result["node2"]["current_sessions"], 3)

    def test_stats_reader_handles_multiple_socket_chunks_and_error_text(self):
        class FakeSocket:
            def __init__(self):
                self.chunks = [b"# pxname,svname,scur,status,last_chk\n",
                               b"pkvm_bank_nodes,node1,0,DOWN,Error: connection refused\n",
                               b"pkvm_bank_nodes,node2,3,UP,ok\n\n"]

            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def settimeout(self, *_):
                pass

            def connect(self, *_):
                pass

            def sendall(self, *_):
                pass

            def recv(self, *_):
                return self.chunks.pop(0) if self.chunks else b""

        with patch.object(ha.socket, "socket", side_effect=lambda *args: FakeSocket()):
            response = ha.command("show stat")
        self.assertIn("Error: connection refused", response)
        self.assertIn("node2,3", response)


if __name__ == "__main__":
    unittest.main()
