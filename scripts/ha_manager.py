#!/usr/bin/env python3
"""HAProxy primary/standby controller with serialized, bounded manual failback."""
import argparse
import csv
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
from contextlib import contextmanager

STATE = Path("/var/lib/personal-kvm/ha-state.json")
SOCK = "/run/pkvm-haproxy/admin.sock"
SPECS = Path("/etc/personal-kvm/ha-services.json")
LOCK = Path("/var/lib/personal-kvm/ha-state.lock")


@contextmanager
def state_lock():
    LOCK.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    fd = os.open(LOCK, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "r+"):
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield


def command(statement):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(5)
        sock.connect(SOCK)
        sock.sendall((statement + "\n").encode())
        if statement == "show stat":
            # A host can run many managed services; one recv(16384) can cut
            # off the desired backend when the CSV contains many rows.
            chunks = []
            total = 0
            while total < 1024 * 1024:
                try:
                    chunk = sock.recv(16384)
                except socket.timeout:
                    if chunks:
                        break
                    raise
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if b"\n\n" in chunk or b"\n\n" in b"".join(chunks[-2:]):
                    break
            if total >= 1024 * 1024:
                raise RuntimeError("HAProxy stats response exceeded safety limit")
            result = b"".join(chunks).decode(errors="replace")
        else:
            result = sock.recv(16384).decode(errors="replace")
        # Stats may legitimately contain 'error' in a health-check reason;
        # it is not an error response from the CLI.
        if statement != "show stat" and ("error" in result.lower() or "unknown" in result.lower()):
            raise RuntimeError("HAProxy runtime rejected command")
        return result


def reachable(host, port, timeout=2):
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def save(obj):
    STATE.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix=".ha-", dir=str(STATE.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(obj, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(path, 0o600)
        os.replace(path, STATE)
    finally:
        if os.path.exists(path):
            os.unlink(path)


def load():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def services():
    data = json.loads(SPECS.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Expected list of L4 services")
    for spec in data:
        nodes = spec.get("backends", [])
        if len(nodes) != 2 or nodes[0].get("standby") or not nodes[1].get("standby"):
            raise ValueError("HA requires primary node1 and backup node2")
    return data


def stats_for(name):
    """Return HAProxy's live backend rows; never infer them from the DB."""
    raw = command("show stat")
    if not raw.lstrip().startswith("# "):
        raise RuntimeError("HAProxy stats response has no CSV header")
    rows = list(csv.DictReader(raw.lstrip()[2:].splitlines()))
    px = f"pkvm_{name}_nodes"
    result = {}
    for row in rows:
        if row.get("pxname") == px and row.get("svname") in ("node1", "node2"):
            try:
                sessions = int(row.get("scur") or 0)
            except ValueError as exc:
                raise RuntimeError("Invalid HAProxy session count") from exc
            result[row["svname"]] = {"status": row.get("status", "UNKNOWN"),
                                      "current_sessions": sessions}
    if not result.get("node1"):
        raise RuntimeError("HAProxy backend statistics unavailable")
    return result


def health(name):
    spec = next((s for s in services() if s["name"] == name), None)
    backends = stats_for(name)
    if spec is None:
        return {"state": "NON_HA", "backends": backends,
                "primary_reachable": None, "failback_available": False}
    if "node2" not in backends:
        raise RuntimeError("Standby HAProxy statistics unavailable")
    with state_lock():
        status = load().get(name, "ACTIVE_PRIMARY")
    primary = spec["backends"][0]
    healthy = reachable(primary["host"], primary["port"])
    return {"state": status, "backends": backends, "primary_reachable": healthy,
            "failback_available": status == "ACTIVE_STANDBY" and healthy}


def apply_once(items):
    with state_lock():
        states = load()
        for spec in items:
            name = spec["name"]
            threshold = spec.get("failover_threshold", 3)
            if not 1 <= threshold <= 20:
                raise ValueError("failover_threshold must be 1..20")
            primary = spec["backends"][0]
            healthy = reachable(primary["host"], primary["port"])
            status = states.get(name, "ACTIVE_PRIMARY")
            if status in ("STANDBY", "ACTIVE_STANDBY"):
                command(f"disable server pkvm_{name}_nodes/node1")
            elif status == "FAILBACK_PENDING":
                # A crashed failback never implicitly enables the primary.
                command(f"disable server pkvm_{name}_nodes/node1")
                states[name] = "ACTIVE_STANDBY"
                states[f"{name}_reason"] = "Recovered interrupted failback"
                save(states)
            elif not healthy:
                failures = int(states.get(f"{name}_failures", 0)) + 1
                states[f"{name}_failures"] = failures
                if failures >= threshold:
                    command(f"disable server pkvm_{name}_nodes/node1")
                    states[name] = "ACTIVE_STANDBY"
                    states[f"{name}_reason"] = f"Primary failed {failures} consecutive health checks"
                    states[f"{name}_updated_at"] = datetime.now(timezone.utc).isoformat()
                else:
                    states[name] = "SUSPECT"
                save(states)
            elif status == "SUSPECT" and healthy:
                states[name] = "ACTIVE_PRIMARY"
                states[f"{name}_failures"] = 0
                save(states)
        return states


def failback(name, *, drain_timeout=30, ready_timeout=15, poll_interval=0.5):
    """Fail back only after all existing standby sessions have drained.

    If the bound is reached, restore the standby and report failure; never
    drop an established TCP connection merely to claim success.
    """
    target = next((x for x in services() if x["name"] == name), None)
    if target is None:
        raise ValueError("Unknown HA service")
    if not 0 < drain_timeout <= 60 or not 0 < ready_timeout <= 30 or poll_interval <= 0:
        raise ValueError("Invalid failback timeout")
    with state_lock():
        current = load()
        if current.get(name) not in ("ACTIVE_STANDBY", "STANDBY"):
            raise RuntimeError("Failback requires ACTIVE_STANDBY state")
        primary = target["backends"][0]
        if not reachable(primary["host"], primary["port"]):
            raise RuntimeError("Primary TCP health check failed; failback refused")
        if "node2" not in stats_for(name):
            raise RuntimeError("Standby HAProxy statistics unavailable")
        current[name] = "FAILBACK_PENDING"
        save(current)
        try:
            command(f"enable server pkvm_{name}_nodes/node1")
            deadline = time.monotonic() + ready_timeout
            while True:
                primary_status = stats_for(name)["node1"]["status"]
                if primary_status == "UP":
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("Primary was not UP before failback deadline")
                time.sleep(poll_interval)
            command(f"set server pkvm_{name}_nodes/node2 state drain")
            deadline = time.monotonic() + drain_timeout
            while True:
                remaining = stats_for(name)["node2"]["current_sessions"]
                if remaining == 0:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Standby drain timed out ({remaining} active connections)")
                time.sleep(poll_interval)
            command(f"set server pkvm_{name}_nodes/node2 state ready")
            current[name] = "ACTIVE_PRIMARY"
            current[f"{name}_failures"] = 0
            current[f"{name}_reason"] = "Manual failback completed after TCP drain"
            current[f"{name}_updated_at"] = datetime.now(timezone.utc).isoformat()
            save(current)
        except Exception:
            # Keep the standby available, even if one rollback step fails.
            for command_text in (f"disable server pkvm_{name}_nodes/node1",
                                 f"set server pkvm_{name}_nodes/node2 state ready"):
                try:
                    command(command_text)
                except Exception:
                    print("HAProxy failback rollback command failed", file=sys.stderr)
            current[name] = "ACTIVE_STANDBY"
            current[f"{name}_reason"] = "Manual failback failed; verify HAProxy runtime"
            save(current)
            raise


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--monitor", action="store_true")
    p.add_argument("--failback")
    args = p.parse_args()
    try:
        if args.failback:
            failback(args.failback)
        elif args.monitor:
            while True:
                try:
                    apply_once(services())
                except (OSError, RuntimeError, ValueError) as exc:
                    print(f"HA monitor warning: {exc}", file=sys.stderr, flush=True)
                time.sleep(3)
        else:
            print(json.dumps(load(), ensure_ascii=False))
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        print(f"HA operation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
