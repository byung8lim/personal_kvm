import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence


class LibvirtError(RuntimeError):
    def __init__(self, message: str, *, command: Optional[Sequence[str]] = None):
        super().__init__(message)
        self.command = list(command or [])


@dataclass(frozen=True)
class CommandResult:
    stdout: str
    stderr: str
    returncode: int


Runner = Callable[[Sequence[str], int], CommandResult]


VIRSH_TO_INSTANCE_STATE: Dict[str, str] = {
    "running": "RUNNING",
    "idle": "RUNNING",
    "paused": "ERROR",
    "in shutdown": "STOPPING",
    "shut off": "STOPPED",
    "crashed": "ERROR",
    "pmsuspended": "STOPPED",
    "blocked": "ERROR",
    "no state": "ERROR",
}


def _subprocess_runner(command: Sequence[str], timeout: int) -> CommandResult:
    env = os.environ.copy()
    env["LIBVIRT_DEFAULT_URI"] = "qemu:///system"
    try:
        completed = subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise LibvirtError(
            f"libvirt 명령이 {timeout}초 안에 완료되지 않았습니다.", command=command
        ) from exc
    except OSError as exc:
        raise LibvirtError(f"libvirt 명령 실행에 실패했습니다: {exc}", command=command) from exc
    return CommandResult(
        stdout=completed.stdout.strip(),
        stderr=completed.stderr.strip(),
        returncode=completed.returncode,
    )


class LibvirtService:
    def __init__(
        self,
        uri: str = "qemu:///system",
        runner: Optional[Runner] = None,
        virsh_path: Optional[str] = None,
    ):
        self.uri = uri
        self.runner = runner or _subprocess_runner
        self.virsh_path = virsh_path or shutil.which("virsh") or "/usr/bin/virsh"

    def _run(self, args: Sequence[str], timeout: int = 30) -> str:
        command = [self.virsh_path, "--connect", self.uri, *args]
        result = self.runner(command, timeout)
        if result.returncode != 0:
            detail = result.stderr or result.stdout or "알 수 없는 libvirt 오류"
            raise LibvirtError(detail, command=command)
        return result.stdout

    def connection_status(self) -> Dict[str, object]:
        try:
            uri = self._run(["uri"], timeout=10)
            version = self._run(["version"], timeout=10)
            return {"connected": True, "uri": uri or self.uri, "version": version}
        except LibvirtError as exc:
            return {"connected": False, "uri": self.uri, "error": str(exc)}

    def list_domains(self) -> List[str]:
        output = self._run(["list", "--all", "--name"])
        return [line.strip() for line in output.splitlines() if line.strip()]

    def domain_exists(self, name: str) -> bool:
        try:
            self._run(["dominfo", name], timeout=10)
            return True
        except LibvirtError:
            return False

    def domain_state(self, name: str) -> str:
        raw = self._run(["domstate", name], timeout=10).strip().lower()
        return VIRSH_TO_INSTANCE_STATE.get(raw, "ERROR")

    def domain_info(self, name: str) -> Dict[str, object]:
        output = self._run(["dominfo", name], timeout=10)
        values = {}
        for line in output.splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                values[key.strip().lower()] = value.strip()
        return {"state": VIRSH_TO_INSTANCE_STATE.get(values.get("state", "").lower(), "ERROR"),
                "vcores": int(values.get("cpu(s)", "0")),
                "memory_gb": round(int(values.get("max memory", "0 KiB").split()[0]) / (1024 ** 2), 2)}

    def start(self, name: str) -> None:
        self._run(["start", name], timeout=60)

    def shutdown(self, name: str) -> None:
        self._run(["shutdown", name], timeout=30)

    def wait_for_state(self, name: str, expected: str, timeout: int = 60, interval: float = 1.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.domain_state(name) == expected:
                return True
            time.sleep(interval)
        return False

    def shutdown_and_wait(self, name: str, timeout: int = 60, force: bool = False) -> None:
        if force:
            self.destroy(name)
            if self.wait_for_state(name, "STOPPED", timeout=10):
                return
            raise LibvirtError(f"{name} 인스턴스를 강제 중지하지 못했습니다.")
        self.shutdown(name)
        if self.wait_for_state(name, "STOPPED", timeout=timeout):
            return
        raise LibvirtError(f"{name} 인스턴스가 {timeout}초 안에 종료되지 않았습니다.")

    def destroy(self, name: str) -> None:
        self._run(["destroy", name], timeout=30)

    def undefine(self, name: str, remove_nvram: bool = True) -> None:
        args = ["undefine", name]
        if remove_nvram:
            args.append("--nvram")
        self._run(args, timeout=30)

    def set_resources(self, name: str, vcores: int, memory_gb: int) -> None:
        current = self.domain_info(name)

        # A persistent domain created with <vcpu>2</vcpu> has both its current
        # and maximum vCPU count set to two.  Growing it therefore requires
        # raising the maximum before changing the current count.  Shrinking is
        # the inverse: lower the current count before lowering the maximum.
        vcpu_current = int(current["vcores"])
        if vcores > vcpu_current:
            self._run(["setvcpus", name, str(vcores), "--maximum", "--config"], timeout=30)
            self._run(["setvcpus", name, str(vcores), "--config"], timeout=30)
        else:
            self._run(["setvcpus", name, str(vcores), "--config"], timeout=30)
            self._run(["setvcpus", name, str(vcores), "--maximum", "--config"], timeout=30)

        memory_current = float(current["memory_gb"])
        if memory_gb > memory_current:
            self._run(["setmaxmem", name, f"{memory_gb}G", "--config"], timeout=30)
            self._run(["setmem", name, f"{memory_gb}G", "--config"], timeout=30)
        else:
            self._run(["setmem", name, f"{memory_gb}G", "--config"], timeout=30)
            self._run(["setmaxmem", name, f"{memory_gb}G", "--config"], timeout=30)


    def attach_disk(self, name: str, path: str, target: str) -> None:
        self._run(["attach-disk", name, path, target, "--config", "--subdriver", "qcow2"], timeout=30)

    def detach_disk(self, name: str, target: str) -> None:
        self._run(["detach-disk", name, target, "--config"], timeout=30)

    def autostart(self, name: str, enabled: bool) -> None:
        args = ["autostart", name]
        if not enabled:
            args.append("--disable")
        self._run(args, timeout=30)

    def define_xml(self, xml: str) -> None:
        path = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".xml", encoding="utf-8", delete=False) as handle:
                handle.write(xml)
                path = handle.name
            self._run(["define", path], timeout=30)
        finally:
            if path and os.path.exists(path):
                os.unlink(path)


libvirt_service = LibvirtService()

