"""Read-only authoritative DNS diagnostics. No TSIG secret returned."""
import ipaddress
import re
import subprocess

_HOST = re.compile(r"^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$")

def validated_name(value):
    if not isinstance(value, str) or not value or len(value) > 253:
        raise ValueError("Invalid DNS domain")
    parts = value.rstrip(".").split(".")
    if not all(_HOST.fullmatch(part) for part in parts):
        raise ValueError("Invalid DNS domain")
    return ".".join(parts).lower() + "."

def query_record(server, fqdn, record_type, *, executor=subprocess.run):
    server_ip = str(ipaddress.ip_address(server))
    fqdn = validated_name(fqdn)
    if record_type not in ("A", "AAAA", "PTR", "SOA"):
        raise ValueError("Unsupported record type")
    result = executor(["dig", "+time=2", "+tries=1", "+noall", "+answer",
                       "@" + server_ip, fqdn, record_type],
                      text=True, capture_output=True, timeout=5, check=False)
    if result.returncode:
        raise RuntimeError("DNS query failed")
    return [x for x in result.stdout.splitlines() if x.strip()][:100]


def inspect_owned_pair(server, hostname, ip, forward_zone, reverse_zone,
                       *, lookup=query_record):
    """Compare the DB's expected A/PTR to live authoritative DNS responses.

    This tests the querying host's source view only; it does not assert that
    every split-DNS client sees the same records.
    """
    address = ipaddress.IPv4Address(ip)
    fqdn = validated_name(hostname + "." + forward_zone.rstrip("."))
    pointer = address.reverse_pointer + "."
    if not pointer.endswith(validated_name(reverse_zone)):
        return {"status": "out_of_zone", "fqdn": fqdn,
                "expected_a": str(address), "expected_ptr": fqdn}
    try:
        a_lines = lookup(server, fqdn, "A")
        ptr_lines = lookup(server, pointer, "PTR")
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired):
        return {"status": "query_failed", "fqdn": fqdn,
                "expected_a": str(address), "expected_ptr": fqdn}
    a = {m.group(1) for line in a_lines
         if (m := re.search(r"\sIN\s+A\s+([\d.]+)\s*$", line, re.I))}
    ptr = {m.group(1).lower() for line in ptr_lines
           if (m := re.search(r"\sIN\s+PTR\s+(\S+)\s*$", line, re.I))}
    wanted = fqdn.lower()
    if a == {str(address)} and ptr == {wanted}:
        status = "ok"
    elif a or ptr:
        status = "conflict" if (a and a != {str(address)}) or (ptr and ptr != {wanted}) else "missing"
    else:
        status = "missing"
    return {"status": status, "fqdn": fqdn, "expected_a": str(address),
            "expected_ptr": wanted, "actual_a": sorted(a), "actual_ptr": sorted(ptr),
            "view": "source_ip_dependent"}

def dns_status(config, *, executor=subprocess.run):
    if not config.get("enabled", False):
        return {"enabled": False, "status": "disabled", "zones": []}
    server = config.get("server")
    if not server:
        return {"enabled": True, "status": "misconfigured", "zones": []}
    zones = []
    for field in ("forward_zone", "reverse_zone"):
        zone = config.get(field)
        if not zone:
            zones.append({"type": field, "status": "missing_zone"})
            continue
        try:
            answer = query_record(server, zone, "SOA", executor=executor)
            zones.append({"type": field, "zone": validated_name(zone),
                          "status": "ok" if answer else "no_answer", "answer": answer})
        except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired):
            zones.append({"type": field, "status": "query_error"})
    status = "ok" if len(zones) == 2 and all(x["status"] == "ok" for x in zones) else "degraded"
    return {"enabled": True, "status": status, "zones": zones}
