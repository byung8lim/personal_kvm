"""Reconcile only VM-owned DNS identities with DNS server state.

Read-only review is the default. Apply changes only via an authenticated
operator action; unrelated DNS entries are never modified.
"""
import ipaddress
import re
from app.services.dns_inspector import query_record

_A = re.compile(r"\sIN\s+A\s+(\S+)\s*$", re.IGNORECASE)
_PTR = re.compile(r"\sIN\s+PTR\s+(\S+)\s*$", re.IGNORECASE)

def _answer(lines, pattern):
    return {m.group(1).rstrip(".").lower() for line in lines if (m := pattern.search(line))}

def review(updater, instances):
    findings = []
    for row in instances:
        if not row.private_ip or not row.dns_hostname or row.state not in ("RUNNING", "STOPPED"):
            continue
        address = str(ipaddress.IPv4Address(row.private_ip))
        fqdn = row.dns_hostname.lower() + "." + updater.forward_zone.rstrip(".")
        ptr = ipaddress.IPv4Address(address).reverse_pointer
        if not (ptr + ".").endswith(updater.reverse_zone):
            findings.append({"instance_id":row.id, "status":"out_of_zone"})
            continue
        try:
            a = _answer(query_record(updater.server, fqdn, "A"), _A)
            reverse = _answer(query_record(updater.server, ptr, "PTR"), _PTR)
        except Exception:
            findings.append({"instance_id":row.id, "status":"query_failed"})
            continue
        expected_ptr = fqdn.lower()
        if address in a and expected_ptr in reverse:
            status = "ok"
        elif (a and a != {address}) or (reverse and reverse != {expected_ptr}):
            # Avoid overwriting an entry which might belong to another owner.
            status = "conflict"
        else:
            status = "missing"
        findings.append({"instance_id":row.id, "hostname":row.dns_hostname,
                         "address":address,"status":status})
    return findings

def apply_missing(updater, instances, *, max_changes=50):
    indexed = {v.id:v for v in instances}
    results = []
    for finding in review(updater, instances):
        if finding["status"] != "missing" or len(results) >= max_changes:
            results.append(finding)
            continue
        row = indexed[finding["instance_id"]]
        try:
            updater.add(row.dns_hostname, row.private_ip)
            results.append({**finding,"status":"repair_submitted"})
        except Exception:
            results.append({**finding,"status":"repair_failed"})
    return results
