import tempfile
import unittest
from pathlib import Path

from unittest.mock import patch

from app.services.vm_provisioner import ProvisioningError, VmProvisioner, resolve_qemu_emulator


class FakeLibvirt:
    def __init__(self, final_state="RUNNING"):
        self.calls = []
        self.final_state = final_state
    def define_xml(self, xml): self.calls.append(("define", xml))
    def autostart(self, name, enabled): self.calls.append(("autostart", name, enabled))
    def start(self, name): self.calls.append(("start", name))
    def domain_state(self, name): return self.final_state
    def destroy(self, name): self.calls.append(("destroy", name))
    def undefine(self, name): self.calls.append(("undefine", name))


class TestProvisioner(VmProvisioner):
    __test__ = False
    def create_root_disk(self, source, destination, size_gb): Path(destination).write_bytes(b"root")
    def create_empty_disk(self, destination, size_gb): Path(destination).write_bytes(b"extra")
    def customize(self, destination, name): pass
    def wait_for_guest_network(self, name, networks, timeout=180): pass


class VmProvisionerTests(unittest.TestCase):
    def test_cloud_init_seed_uses_genisoimage_when_cloud_localds_is_unavailable(self):
        completed = type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        captured = {}
        def capture_run(*args, **kwargs):
            captured["user_data"] = Path(kwargs["cwd"], "user-data").read_text(encoding="utf-8")
            captured["network_config"] = Path(kwargs["cwd"], "network-config").read_text(encoding="utf-8")
            return completed
        with tempfile.TemporaryDirectory() as folder, \
             patch("app.services.vm_provisioner.shutil.which", side_effect=lambda name: "/usr/bin/genisoimage" if name == "genisoimage" else None), \
             patch("app.services.vm_provisioner.subprocess.run", side_effect=capture_run) as run:
            destination = str(Path(folder) / "seed.iso")
            key_path = Path(folder) / "id_rsa.pub"
            key_path.write_text("ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQDiagnostic test@pkvm\n", encoding="utf-8")
            VmProvisioner(FakeLibvirt(), emulator_path="/usr/bin/qemu-system-x86_64",
                          ssh_public_key_path=str(key_path)).create_cloud_init_seed(
                destination, "vm-seed", [{"mac": "52:54:00:00:00:01", "address": "10.0.0.2/24",
                                           "gateway": "10.0.0.1", "default": True}],
                "kvmadmin", "StrongPass123!"
            )
            command = run.call_args.args[0]
            self.assertEqual(command[0], "/usr/bin/genisoimage")
            self.assertIn("network-config", command)
            self.assertIsNotNone(run.call_args.kwargs["cwd"])
            self.assertIn("resize_rootfs: true", captured["user_data"])
            self.assertIn("manage_etc_hosts: false", captured["user_data"])
            self.assertIn("ssh-rsa AAAAB3", captured["user_data"])
            self.assertIn("to: 0.0.0.0/0", captured["network_config"])
            self.assertNotIn("to: default", captured["network_config"])

    def test_cloud_init_seed_uses_xorriso_when_other_iso_tools_missing(self):
        completed = type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        with tempfile.TemporaryDirectory() as folder, \
             patch("app.services.vm_provisioner.shutil.which", side_effect=lambda name: "/usr/bin/xorriso" if name == "xorriso" else None), \
             patch("app.services.vm_provisioner.subprocess.run", return_value=completed) as run:
            key_path = Path(folder) / "id_rsa.pub"
            key_path.write_text("ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQDiagnostic test@pkvm\n")
            VmProvisioner(FakeLibvirt(), emulator_path="/usr/bin/qemu-system-x86_64",
                          ssh_public_key_path=str(key_path)).create_cloud_init_seed(
                str(Path(folder) / "seed.iso"), "vm-seed",
                [{"mac": "52:54:00:00:00:01", "address": "10.0.0.2/24"}],
                "kvmadmin", "StrongPass123!"
            )
            args = run.call_args.args[0]
            self.assertEqual(args[:3], ["/usr/bin/xorriso", "-as", "mkisofs"])
            self.assertIn("network-config", args)

    def test_emulator_is_discovered_from_libvirt_capabilities(self):
        completed = type("Completed", (), {"returncode": 0, "stdout": "<domainCapabilities><path>/opt/qemu/bin/qemu-system-x86_64</path></domainCapabilities>"})()
        with patch("app.services.vm_provisioner.shutil.which", return_value="/usr/bin/virsh"), \
             patch("app.services.vm_provisioner.Path.is_file", return_value=True), \
             patch("app.services.vm_provisioner.os.access", return_value=True):
            self.assertEqual(resolve_qemu_emulator(run=lambda *args, **kwargs: completed), "/opt/qemu/bin/qemu-system-x86_64")

    def test_invalid_configured_emulator_fails_closed(self):
        with patch("app.services.vm_provisioner.Path.is_file", return_value=False):
            with self.assertRaises(ProvisioningError):
                resolve_qemu_emulator("/missing/qemu")

    def test_custom_emulator_path_is_written_to_domain_xml(self):
        service = VmProvisioner(FakeLibvirt(), emulator_path="/opt/qemu/bin/qemu-system-x86_64")
        xml = service.domain_xml("vm-emulator", 2, 1, ["/tmp/root.qcow2"], ["virbr1"])
        self.assertIn("/opt/qemu/bin/qemu-system-x86_64", xml)

    def test_xml_contains_resources_disks_and_networks(self):
        service = VmProvisioner(FakeLibvirt(), emulator_path="/usr/bin/qemu-system-x86_64")
        xml = service.domain_xml("vm-01", 4, 2, ["/data/root.qcow2"], ["virbr1", "virbr2"],
                                 ["52:54:00:00:00:01", "52:54:00:00:00:02"], "/data/seed.iso")
        self.assertIn("<vcpu placement=\"static\">2</vcpu>", xml)
        self.assertIn("<cpu mode=\"host-passthrough\" check=\"none\"", xml)
        self.assertIn("/data/root.qcow2", xml)
        self.assertIn("virbr1", xml)
        self.assertIn("52:54:00:00:00:01", xml)
        self.assertIn("/data/seed.iso", xml)

    def test_failure_rolls_back_domain_and_files(self):
        with tempfile.TemporaryDirectory() as folder:
            libvirt = FakeLibvirt(final_state="ERROR")
            service = TestProvisioner(libvirt, emulator_path="/usr/bin/qemu-system-x86_64")
            root, extra = str(Path(folder) / "root.qcow2"), str(Path(folder) / "extra.qcow2")
            with self.assertRaises(ProvisioningError):
                service.provision("vm-01", "ignored", root, 20, extra, 10, 4, 2, ["virbr1"])
            self.assertFalse(Path(root).exists())
            self.assertFalse(Path(extra).exists())
            self.assertIn(("undefine", "vm-01"), libvirt.calls)

    def test_failed_root_conversion_removes_partial_disk(self):
        service = VmProvisioner(FakeLibvirt())
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.qcow2"
            dest = Path(folder) / "root.qcow2"
            source.write_bytes(b"source")
            def fail_after_partial_write(args):
                dest.write_bytes(b"incomplete")
                raise ProvisioningError("convert failed")
            with patch.object(service, "_qemu", side_effect=fail_after_partial_write):
                with self.assertRaises(ProvisioningError):
                    service.create_root_disk(str(source), str(dest), 20)
            self.assertFalse(dest.exists())

    def test_failed_extra_disk_creation_removes_partial_disk(self):
        service = VmProvisioner(FakeLibvirt())
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder) / "extra.qcow2"
            def fail_after_partial_write(args):
                dest.write_bytes(b"incomplete")
                raise ProvisioningError("create failed")
            with patch.object(service, "_qemu", side_effect=fail_after_partial_write):
                with self.assertRaises(ProvisioningError):
                    service.create_empty_disk(str(dest), 10)
            self.assertFalse(dest.exists())

    def test_cleanup_compensates_domain_and_all_files(self):
        libvirt = FakeLibvirt(final_state="RUNNING")
        service = VmProvisioner(libvirt)
        with tempfile.TemporaryDirectory() as folder:
            paths = [str(Path(folder) / name) for name in ("root.qcow2", "extra.qcow2", "seed.iso")]
            for path in paths:
                Path(path).write_bytes(b"created")
            service.cleanup("vm-01", paths)
            self.assertIn(("destroy", "vm-01"), libvirt.calls)
            self.assertIn(("undefine", "vm-01"), libvirt.calls)
            self.assertTrue(all(not Path(path).exists() for path in paths))

    def test_guest_network_waits_until_an_assigned_ip_replies(self):
        service = VmProvisioner(FakeLibvirt(), emulator_path="/usr/bin/qemu-system-x86_64")
        failed = type("Completed", (), {"returncode": 1})()
        replied = type("Completed", (), {"returncode": 0})()
        with patch("app.services.vm_provisioner.shutil.which", return_value="/usr/bin/ping"), \
             patch("app.services.vm_provisioner.time.monotonic", side_effect=[0, 1, 2]), \
             patch("app.services.vm_provisioner.time.sleep"), \
             patch("app.services.vm_provisioner.subprocess.run", side_effect=[failed, replied, replied, replied]) as run:
            service.wait_for_guest_network(
                "vm-01",
                [{"address": "10.10.50.2/24"}, {"address": "10.10.60.2/24"}],
                timeout=10,
            )
        self.assertEqual(run.call_count, 4)


if __name__ == "__main__": unittest.main()
