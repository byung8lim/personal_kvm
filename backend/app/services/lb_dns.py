"""DNS ownership and lifecycle helpers for managed L4/L7 services."""
from dataclasses import dataclass
import ipaddress
import re

from app.services.bind9_ddns import DdnsError
from app.services.dns_inspector import query_record, validated_name


_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


@dataclass(frozen=True)
class LbDnsBinding:
    hostname: str
    fqdn: str
    address: str
    zone_kind: str


def service_domain(spec):
    if spec.get("layer") == "L7":
        value = spec.get("domain")
    else:
        value = spec.get("domain") or spec.get("sni_domain")
    if not isinstance(value, str):
        raise ValueError("Managed LB service domain is required")
    return validated_name(value).rstrip(".")


def binding_for(spec, config):
    """Return the single managed-zone A record owned by an LB service."""
    dns = config.get("dns") or {}
    if not dns.get("enabled"):
        raise DdnsError("DNS updates are disabled")
    domain = service_domain(spec)
    private = (dns.get("private_domain") or dns.get("forward_zone") or "").rstrip(".").lower()
    external = (dns.get("external_domain") or "").rstrip(".").lower()
    matches = [("private", private), ("external", external)]
    zone_kind = zone = hostname = None
    for candidate_kind, candidate_zone in sorted(matches, key=lambda item: len(item[1]), reverse=True):
        suffix = "." + candidate_zone
        if candidate_zone and domain.endswith(suffix):
            relative = domain[:-len(suffix)]
            if _LABEL.fullmatch(relative):
                zone_kind, zone, hostname = candidate_kind, candidate_zone, relative.lower()
                break
    if not hostname:
        raise ValueError("LB domain must be one label below a managed DNS zone")

    if spec.get("layer") == "L4":
        address = str(ipaddress.IPv4Address(spec.get("listen_ip")))
    elif spec.get("layer") == "L7":
        networks = config.get("networks") or {}
        if zone_kind == "external":
            address = str(ipaddress.ip_interface(networks["bridged"]["host_ip_cidr"]).ip)
        else:
            internal = networks["internal"]
            address = str(internal.get("dns") or next(ipaddress.ip_network(internal["cidr"]).hosts()))
        ipaddress.IPv4Address(address)
    else:
        raise ValueError("layer must be L4 or L7")
    return LbDnsBinding(hostname, f"{hostname}.{zone}.", address, zone_kind)


def _a_values(lines):
    values = set()
    for line in lines:
        match = re.search(r"\sIN\s+A\s+([\d.]+)\s*$", line, re.I)
        if match:
            values.add(str(ipaddress.IPv4Address(match.group(1))))
    return values


def ensure_a(binding, updater, *, lookup=query_record):
    """Create the A record if absent; reject records owned by something else."""
    current = _a_values(lookup(updater.server, binding.fqdn, "A"))
    if current == {binding.address}:
        return False
    if current:
        raise DdnsError("LB DNS name already has a different A record")
    updater.add_a(binding.hostname, binding.address)
    return True


def remove_a_if_owned(binding, updater, *, lookup=query_record):
    """Remove only the exact A record represented by this service binding."""
    current = _a_values(lookup(updater.server, binding.fqdn, "A"))
    if not current:
        return False
    if current != {binding.address}:
        raise DdnsError("Refusing to remove DNS record not owned by this LB service")
    updater.remove_a(binding.hostname, binding.address)
    return True
