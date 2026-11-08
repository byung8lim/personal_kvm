"""Constrained Bind9 RFC 2136 updates using an administrator-managed TSIG key.

The module deliberately does not discover or change guest hostnames.  Its DNS
identity must come from the persisted creation-time Instance.dns_hostname.
"""
import ipaddress
from pathlib import Path
import re
import subprocess


_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


class DdnsError(RuntimeError):
    pass


def _fqdn(hostname: str, zone: str) -> str:
    if not _LABEL.fullmatch(hostname):
        raise ValueError("Invalid DNS hostname")
    labels = zone.rstrip(".").split(".")
    if not labels or any(not _LABEL.fullmatch(label) for label in labels):
        raise ValueError("Invalid DNS zone")
    return f"{hostname.lower()}.{'.'.join(labels).lower()}."


class Bind9Ddns:
    def __init__(self, server: str, key_file: str, forward_zone: str, reverse_zone: str,
                 ttl: int = 300, nsupdate: str = "/usr/bin/nsupdate"):
        self.server = ipaddress.ip_address(server).compressed
        self.key_file = Path(key_file)
        self.forward_zone = forward_zone.rstrip(".").lower() + "."
        self.reverse_zone = reverse_zone.rstrip(".").lower() + "."
        if not 30 <= ttl <= 86400:
            raise ValueError("DNS TTL must be 30..86400 seconds")
        self.ttl = ttl
        self.nsupdate = nsupdate

    def _run(self, update: str) -> None:
        if not self.key_file.is_file():
            raise DdnsError("Configured Bind9 TSIG key file is missing")
        result = subprocess.run([self.nsupdate, "-k", str(self.key_file)], input=update,
                                text=True, capture_output=True, timeout=15, check=False)
        if result.returncode:
            # stderr can contain server or key details; do not leak it to API/logs.
            raise DdnsError("Bind9 dynamic update failed")

    def _update(self, zone: str, statements: list[str]) -> None:
        self._run("\n".join([f"server {self.server}", f"zone {zone}", *statements, "send", ""]))

    def add(self, hostname: str, address: str) -> None:
        """Add A and PTR with compensating A removal if PTR update fails.

        Caller must ensure it owns this name and address before invoking.
        """
        ip = ipaddress.IPv4Address(address)
        fqdn = _fqdn(hostname, self.forward_zone)
        ptr = ip.reverse_pointer + "."
        if not ptr.endswith(self.reverse_zone):
            raise ValueError("IP outside configured reverse DNS zone")
        self._update(self.forward_zone, [f"update add {fqdn} {self.ttl} A {ip}"])
        try:
            self._update(self.reverse_zone, [f"update add {ptr} {self.ttl} PTR {fqdn}"])
        except Exception:
            try:
                self._update(self.forward_zone, [f"update delete {fqdn} A {ip}"])
            except Exception:
                pass  # Reconciler must handle the remaining A record.
            raise

    def add_a(self, hostname: str, address: str) -> None:
        """Add an A record without changing the canonical private PTR."""
        ip = ipaddress.IPv4Address(address)
        fqdn = _fqdn(hostname, self.forward_zone)
        self._update(self.forward_zone, [f"update add {fqdn} {self.ttl} A {ip}"])

    def remove_a(self, hostname: str, address: str) -> None:
        ip = ipaddress.IPv4Address(address)
        fqdn = _fqdn(hostname, self.forward_zone)
        self._update(self.forward_zone, [f"update delete {fqdn} A {ip}"])

    def remove(self, hostname: str, address: str) -> None:
        ip = ipaddress.IPv4Address(address)
        fqdn = _fqdn(hostname, self.forward_zone)
        ptr = ip.reverse_pointer + "."
        if not ptr.endswith(self.reverse_zone):
            raise ValueError("IP outside configured reverse DNS zone")
        self._update(self.reverse_zone, [f"update delete {ptr} PTR {fqdn}"])
        self._update(self.forward_zone, [f"update delete {fqdn} A {ip}"])
