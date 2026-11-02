import unittest

from app.services.instance_state import InvalidStateTransition, ensure_transition


class InstanceStateTests(unittest.TestCase):
    def test_valid_transition(self):
        ensure_transition("RUNNING", "STOPPING")
        ensure_transition("STOPPING", "STOPPED")
        ensure_transition("STOPPED", "DELETING")
        ensure_transition("DELETING", "DELETED")

    def test_same_state_is_idempotent(self):
        ensure_transition("RUNNING", "RUNNING")

    def test_invalid_transition(self):
        with self.assertRaises(InvalidStateTransition):
            ensure_transition("RUNNING", "DELETED")


if __name__ == "__main__":
    unittest.main()

