import os
from passlib.hash import sha512_crypt
import yaml
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import List, Sequence
from xml.etree import ElementTree as ET

from app.services.libvirt_service import LibvirtError, LibvirtService


NAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,62}$")
QEMU_EMULATOR_CANDIDATES = (
    "/usr/libexec/qemu-kvm",
    "/usr/bin/qemu-kvm",
    "/usr/bin/qemu-system-x86_64",
)


class ProvisioningError(RuntimeError):
    pass


def resolve_qemu_emulator(configured_path: str = None, run=subprocess.run) -> str:
    """Resolve an executable supported by libvirt, failing closed when none exists."""
    configured = configured_path or os.getenv("QEMU_EMULATOR_PATH")
    if configured:
        path = Path(configured)
        if not path.is_file() or not os.access(path, os.X_OK):
            raise ProvisioningError(f"설정된 QEMU emulator를 실행할 수 없습니다: {configured}")
        return str(path)
    virsh = shutil.which("virsh")
    if virsh:
        result = run([virsh, "--connect", os.getenv("LIBVIRT_DEFAULT_URI", "qemu:///system"),
                      "domcapabilities", "--arch", "x86_64", "--virttype", "kvm"],
                     capture_output=True, text=True, timeout=30, check=False)
        if result.returncode == 0:
            try:
                value = ET.fromstring(result.stdout).findtext("path")
                if value and Path(value).is_file() and os.access(value, os.X_OK):
                    return value
            except ET.ParseError:
                pass
    for command in ("qemu-kvm", "qemu-system-x86_64"):
        value = shutil.which(command)
        if value and os.access(value, os.X_OK):
            return value
    for value in QEMU_EMULATOR_CANDIDATES:
        if Path(value).is_file() and os.access(value, os.X_OK):
            return value
    raise ProvisioningError("실행 가능한 KVM QEMU emulator를 찾지 못했습니다. QEMU_EMULATOR_PATH 또는 libvirt domcapabilities를 확인하십시오.")


class VmProvisioner:
    def __init__(self, libvirt: LibvirtService, qemu_img: str = None, customize_script: str = None,
                 emulator_path: str = None, ssh_public_key_path: str = None):
        self.libvirt = libvirt
        self.qemu_img = qemu_img or shutil.which("qemu-img") or "/usr/bin/qemu-img"
        self.customize_script = customize_script or "/opt/personal-kvm/scripts/customize_guest.sh"
        self.emulator_path = emulator_path
        self.ssh_public_key_path = ssh_public_key_path or os.getenv(
            "SSH_PUBLIC_KEY_PATH", "/var/lib/personal-kvm/.ssh/id_rsa.pub"
        )

    def _qemu(self, args: Sequence[str]) -> None:
        result = subprocess.run([self.qemu_img, *args], capture_output=True, text=True, timeout=600, check=False)
        if result.returncode:
            raise ProvisioningError(result.stderr.strip() or "qemu-img 실행 실패")

    def create_root_disk(self, source: str, destination: str, size_gb: int) -> None:
        src, dst = Path(source), Path(destination)
        if not src.is_file():
            raise ProvisioningError(f"OS 이미지 파일이 없습니다: {src}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            raise ProvisioningError(f"디스크 파일이 이미 존재합니다: {dst}")
        try:
            self._qemu(["convert", "-p", "-O", "qcow2", str(src), str(dst)])
            self._qemu(["resize", str(dst), f"{size_gb}G"])
        except Exception:
            # A failed qemu-img convert/resize may leave an incomplete image.
            dst.unlink(missing_ok=True)
            raise

    def create_empty_disk(self, destination: str, size_gb: int) -> None:
        dst = Path(destination)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            raise ProvisioningError(f"디스크 파일이 이미 존재합니다: {dst}")
        try:
            self._qemu(["create", "-f", "qcow2", str(dst), f"{size_gb}G"])
        except Exception:
            dst.unlink(missing_ok=True)
            raise

    def customize(self, destination: str, name: str) -> None:
        result = subprocess.run([self.customize_script, destination, name], capture_output=True, text=True,
                                timeout=600, check=False)
        if result.returncode:
            raise ProvisioningError(result.stderr.strip() or "게스트 이미지 커스터마이즈 실패")

    def create_cloud_init_seed(self, destination: str, name: str, networks: List[dict], guest_username: str = "kvmadmin", guest_password: str = None, guest_hostname: str = None, shared_mounts: List[dict] = None, nfs_package: str = None) -> None:
        destination_path = Path(destination)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as folder:
            folder_path = Path(folder)
            (folder_path / "meta-data").write_text(f"instance-id: {name}\nlocal-hostname: {guest_hostname or name}\n", encoding="utf-8")
            public_key_path = Path(self.ssh_public_key_path)
            if not public_key_path.is_file():
                raise ProvisioningError(f"SSH public key file does not exist: {public_key_path}")
            public_key = public_key_path.read_text(encoding="utf-8").strip()
            if not public_key.startswith(("ssh-rsa ", "ssh-ed25519 ", "ecdsa-sha2-")):
                raise ProvisioningError(f"SSH public key is invalid: {public_key_path}")
            if not guest_password:
                raise ProvisioningError("VM 콘솔 계정의 초기 비밀번호를 지정해야 합니다.")
            # Store only a salted SHA-512 hash inside the cloud-init ISO.
            password_hash = sha512_crypt.hash(guest_password)
            user_config = {
                # Cloud-init must not replace the guest's preexisting /etc/hosts.
                "preserve_hostname": False, "hostname": guest_hostname or name, "manage_etc_hosts": False,
                "ssh_pwauth": True, "disable_root": True,
                "users": [{"name": guest_username, "gecos": "KVM administrator",
                           "groups": "wheel,sudo", "shell": "/bin/bash", "lock_passwd": False,
                           "passwd": password_hash, "sudo": "ALL=(ALL) NOPASSWD:ALL",
                           "ssh_authorized_keys": [public_key]}],
                "chpasswd": {"expire": False},
                "growpart": {"mode": "auto", "devices": ["/"]}, "resize_rootfs": True,
                "write_files": [{"path": "/etc/ssh/sshd_config.d/90-personal-kvm.conf",
                                 "permissions": "0644", "content": "PasswordAuthentication yes\nPermitRootLogin no\n"}],
                "runcmd": [["systemctl", "enable", "--now", "sshd"]],
            }
            if shared_mounts:
                if nfs_package not in ("nfs-utils", "nfs-common"):
                    raise ProvisioningError("지원하지 않는 게스트 NFS 패키지입니다.")
                user_config["packages"] = [nfs_package]
                user_config["bootcmd"] = [["mkdir", "-p", mount["target"]] for mount in shared_mounts]
                user_config["mounts"] = [
                    [mount["source"], mount["target"], "nfs4",
                     "defaults,_netdev,nofail,x-systemd.automount", "0", "0"]
                    for mount in shared_mounts
                ]
            (folder_path / "user-data").write_text("#cloud-config\n" + yaml.safe_dump(user_config, allow_unicode=True, sort_keys=False), encoding="utf-8")
            ethernets = {}
            default_route_added = False
            for index, net in enumerate(networks):
                iface = f"eth{index}"
                item = {"match": {"macaddress": net["mac"].lower()},
                        "set-name": iface, "addresses": [net["address"]]}
                if net.get("gateway") and net.get("default") and not default_route_added:
                    # Rocky 9's cloud-init 24.4 does not accept the netplan
                    # shorthand `default` here; it expects a CIDR network.
                    item["routes"] = [{"to": "0.0.0.0/0", "via": net["gateway"]}]
                    default_route_added = True
                if net.get("dns"):
                    item["nameservers"] = {"addresses": [str(net["dns"])]}
                    if net.get("search"):
                        item["nameservers"]["search"] = list(net["search"])
                ethernets[iface] = item
            network_path = folder_path / "network-config"
            network_path.write_text(yaml.safe_dump({"version": 2, "ethernets": ethernets}, sort_keys=False), encoding="utf-8")
            cloud_localds = shutil.which("cloud-localds")
            genisoimage = shutil.which("genisoimage")
            xorriso = shutil.which("xorriso")
            if cloud_localds:
                command = [cloud_localds, "--network-config", str(network_path), str(destination_path),
                           str(folder_path / "user-data"), str(folder_path / "meta-data")]
                cwd = None
            elif genisoimage:
                command = [genisoimage, "-quiet", "-output", str(destination_path), "-volid", "cidata",
                           "-joliet", "-rock", "user-data", "meta-data", "network-config"]
                cwd = str(folder_path)
            elif xorriso:
                command = [xorriso, "-as", "mkisofs", "-quiet", "-output", str(destination_path),
                           "-volid", "cidata", "-joliet", "-rock", "user-data", "meta-data", "network-config"]
                cwd = str(folder_path)
            else:
                raise ProvisioningError("cloud-init seed 생성에 cloud-localds/genisoimage/xorriso 중 하나가 필요합니다.")
            result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=120, check=False)
            if result.returncode:
                destination_path.unlink(missing_ok=True)
                raise ProvisioningError(result.stderr.strip() or "cloud-init seed 생성 실패")

    def wait_for_guest_network(self, name: str, networks: List[dict], timeout: int = 180) -> None:
        """Wait for guest networking, not merely the qemu process, to be ready."""
        addresses = [str(net.get("address", "")).split("/", 1)[0] for net in networks]
        addresses = [address for address in addresses if address]
        ping = shutil.which("ping")
        if not addresses or not ping:
            raise ProvisioningError("게스트 네트워크 확인에 필요한 IP 또는 ping 명령이 없습니다.")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.libvirt.domain_state(name) != "RUNNING":
                raise ProvisioningError("네트워크 준비 중 VM이 실행 상태를 벗어났습니다.")
            responsive = True
            for address in addresses:
                result = subprocess.run(
                    [ping, "-c", "1", "-W", "1", address],
                    capture_output=True, text=True, timeout=3, check=False,
                )
                if result.returncode != 0:
                    responsive = False
            if responsive:
                return
            time.sleep(2)
        raise ProvisioningError(
            f"VM은 시작되었지만 {timeout}초 안에 할당 IP({', '.join(addresses)})로 응답하지 않았습니다."
        )

    def domain_xml(self, name: str, memory_gb: int, vcores: int, disks: List[str], bridges: List[str], macs: List[str] = None, seed_path: str = None) -> str:
        if not NAME_PATTERN.fullmatch(name):
            raise ProvisioningError("인스턴스 이름은 영문·숫자로 시작하고 영문·숫자·._-만 사용할 수 있습니다.")
        domain = ET.Element("domain", {"type": "kvm"})
        ET.SubElement(domain, "name").text = name
        ET.SubElement(domain, "memory", {"unit": "GiB"}).text = str(memory_gb)
        ET.SubElement(domain, "vcpu", {"placement": "static"}).text = str(vcores)
        # Rocky/RHEL 9+ requires the x86-64-v2 CPU baseline.  Libvirt's
        # implicit qemu64 model is older and leaves the qemu process running
        # while the guest kernel cannot boot, which looks like a NIC failure.
        ET.SubElement(domain, "cpu", {"mode": "host-passthrough", "check": "none"})
        os_node = ET.SubElement(domain, "os")
        ET.SubElement(os_node, "type", {"arch": "x86_64", "machine": "q35"}).text = "hvm"
        features = ET.SubElement(domain, "features"); ET.SubElement(features, "acpi"); ET.SubElement(features, "apic")
        devices = ET.SubElement(domain, "devices")
        ET.SubElement(devices, "emulator").text = self.emulator_path or resolve_qemu_emulator()
        for index, path in enumerate(disks):
            disk = ET.SubElement(devices, "disk", {"type": "file", "device": "disk"})
            ET.SubElement(disk, "driver", {"name": "qemu", "type": "qcow2"})
            ET.SubElement(disk, "source", {"file": path})
            ET.SubElement(disk, "target", {"dev": f"vd{chr(ord('a') + index)}", "bus": "virtio"})
        if seed_path:
            seed = ET.SubElement(devices, "disk", {"type": "file", "device": "cdrom"})
            ET.SubElement(seed, "driver", {"name": "qemu", "type": "raw"})
            ET.SubElement(seed, "source", {"file": seed_path})
            ET.SubElement(seed, "target", {"dev": "sda", "bus": "sata"})
            ET.SubElement(seed, "readonly")
        for index, bridge in enumerate(bridges):
            interface = ET.SubElement(devices, "interface", {"type": "bridge"})
            if macs:
                ET.SubElement(interface, "mac", {"address": macs[index]})
            ET.SubElement(interface, "source", {"bridge": bridge})
            ET.SubElement(interface, "model", {"type": "virtio"})
        serial = ET.SubElement(devices, "serial", {"type": "pty"}); ET.SubElement(serial, "target", {"port": "0"})
        console = ET.SubElement(devices, "console", {"type": "pty"}); ET.SubElement(console, "target", {"type": "serial", "port": "0"})
        return ET.tostring(domain, encoding="unicode")

    def provision(self, name: str, image: str, root_path: str, root_gb: int, extra_path: str,
                  extra_gb: int, memory_gb: int, vcores: int, bridges: List[str], macs: List[str] = None,
                  network_configs: List[dict] = None, seed_path: str = None, guest_username: str = "kvmadmin", guest_password: str = None, progress=None, guest_hostname: str = None, shared_mounts: List[dict] = None, nfs_package: str = None) -> None:
        created = []
        defined = False
        try:
            if progress: progress("ROOT_DISK", "루트 디스크 생성 중")
            self.create_root_disk(image, root_path, root_gb); created.append(root_path)
            if progress: progress("OS_CUSTOMIZE", "게스트 OS 초기화 중")
            self.customize(root_path, name)
            disks = [root_path]
            if extra_gb:
                if progress: progress("EXTRA_DISK", "추가 디스크 생성 중")
                self.create_empty_disk(extra_path, extra_gb); created.append(extra_path); disks.append(extra_path)
            if network_configs and seed_path:
                if progress: progress("CLOUD_INIT", "계정 및 네트워크 설정 ISO 생성 중")
                self.create_cloud_init_seed(seed_path, name, network_configs, guest_username, guest_password, guest_hostname=guest_hostname, shared_mounts=shared_mounts, nfs_package=nfs_package); created.append(seed_path)
            if progress: progress("DEFINE", "libvirt 도메인 등록 중")
            self.libvirt.define_xml(self.domain_xml(name, memory_gb, vcores, disks, bridges, macs, seed_path)); defined = True
            self.libvirt.autostart(name, True)
            if progress: progress("BOOT", "VM 부팅 중")
            self.libvirt.start(name)
            if network_configs:
                if progress: progress("NETWORK", "게스트 네트워크 연결 확인 중")
                self.wait_for_guest_network(name, network_configs)
            if self.libvirt.domain_state(name) != "RUNNING":
                raise ProvisioningError("VM 시작 후 RUNNING 상태를 확인하지 못했습니다.")
        except Exception as exc:
            self.cleanup(name, created, domain_defined=defined)
            if isinstance(exc, ProvisioningError): raise
            raise ProvisioningError(str(exc)) from exc

    def cleanup(self, name: str, paths: Sequence[str], domain_defined: bool = True) -> None:
        """Best-effort compensation for a provisioned VM.

        Provisioning touches libvirt and the filesystem outside the database
        transaction.  Callers must use this when database finalization fails
        after ``provision`` returned successfully.
        """
        if domain_defined:
            try:
                if self.libvirt.domain_state(name) != "STOPPED":
                    self.libvirt.destroy(name)
            except LibvirtError:
                pass
            try:
                self.libvirt.undefine(name)
            except LibvirtError:
                pass
        for path in reversed([value for value in paths if value]):
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass


