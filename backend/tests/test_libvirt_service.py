import unittest

from app.services.libvirt_service import CommandResult, LibvirtError, LibvirtService


class FakeRunner:
    def __init__(self, responses):
        self.responses = list(responses)
        self.commands = []

    def __call__(self, command, timeout):
        self.commands.append((list(command), timeout))
        return self.responses.pop(0)


class LibvirtServiceTests(unittest.TestCase):
    def test_connection_status(self):
        runner = FakeRunner([
            CommandResult("qemu:///system", "", 0),
            CommandResult("Compiled against library: libvirt 10.0.0", "", 0),
        ])
        service = LibvirtService(runner=runner, virsh_path="virsh")
        result = service.connection_status()
        self.assertTrue(result["connected"])
        self.assertEqual(result["uri"], "qemu:///system")

    def test_domain_state_mapping(self):
        runner = FakeRunner([CommandResult("shut off", "", 0)])
        service = LibvirtService(runner=runner, virsh_path="virsh")
        self.assertEqual(service.domain_state("vm-01"), "STOPPED")
        self.assertEqual(runner.commands[0][0][-2:], ["domstate", "vm-01"])

    def test_command_failure_does_not_leak_shell_execution(self):
        runner = FakeRunner([CommandResult("", "domain not found", 1)])
        service = LibvirtService(runner=runner, virsh_path="virsh")
        with self.assertRaises(LibvirtError):
            service.domain_state("vm; rm -rf /")
        self.assertEqual(runner.commands[0][0][-1], "vm; rm -rf /")

    def test_lifecycle_commands_use_argument_arrays(self):
        runner = FakeRunner([
            CommandResult("", "", 0),
            CommandResult("", "", 0),
            CommandResult("", "", 0),
        ])
        service = LibvirtService(runner=runner, virsh_path="virsh")
        service.start("vm-01")
        service.shutdown("vm-01")
        service.undefine("vm-01")
        self.assertEqual(runner.commands[0][0][-2:], ["start", "vm-01"])
        self.assertEqual(runner.commands[1][0][-2:], ["shutdown", "vm-01"])
        self.assertEqual(runner.commands[2][0][-3:], ["undefine", "vm-01", "--nvram"])

    def test_shutdown_waits_for_stopped_state(self):
        runner = FakeRunner([CommandResult("", "", 0), CommandResult("shut off", "", 0)])
        service = LibvirtService(runner=runner, virsh_path="virsh")
        service.shutdown_and_wait("vm-01", timeout=1)
        self.assertEqual(runner.commands[0][0][-2:], ["shutdown", "vm-01"])

    def test_force_shutdown_destroys_without_graceful_wait(self):
        runner = FakeRunner([CommandResult("", "", 0), CommandResult("shut off", "", 0)])
        service = LibvirtService(runner=runner, virsh_path="virsh")
        service.shutdown_and_wait("vm-01", force=True)
        self.assertEqual(runner.commands[0][0][-2:], ["destroy", "vm-01"])

    def test_resource_growth_raises_maximums_before_current_values(self):
        runner = FakeRunner([
            CommandResult("State: shut off\nCPU(s): 2\nMax memory: 4194304 KiB", "", 0),
            CommandResult("", "", 0),
            CommandResult("", "", 0),
            CommandResult("", "", 0),
            CommandResult("", "", 0),
        ])
        service = LibvirtService(runner=runner, virsh_path="virsh")
        service.set_resources("vm-01", 4, 8)
        commands = [call[0] for call in runner.commands]
        self.assertEqual(commands[1][-5:], ["setvcpus", "vm-01", "4", "--maximum", "--config"])
        self.assertEqual(commands[2][-4:], ["setvcpus", "vm-01", "4", "--config"])
        self.assertEqual(commands[3][-4:], ["setmaxmem", "vm-01", "8G", "--config"])
        self.assertEqual(commands[4][-4:], ["setmem", "vm-01", "8G", "--config"])


if __name__ == "__main__":
    unittest.main()
