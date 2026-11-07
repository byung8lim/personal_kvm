#!/usr/bin/env python3
"""HAProxy primary/standby controller.

Monitor checks TCP reachability of the primary and puts it in runtime
maintenance after failure. Once failed over, it stays disabled until an
operator explicitly invokes --failback. The state survives restarts.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

STATE=Path("/var/lib/personal-kvm/ha-state.json")
SOCK="/run/pkvm-haproxy/admin.sock"
SPECS=Path("/etc/personal-kvm/ha-services.json")

def command(statement):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
        sock.settimeout(5)
        sock.connect(SOCK)
        sock.sendall((statement+"\n").encode())
        result=sock.recv(16384).decode(errors="replace")
        if "error" in result.lower() or "unknown" in result.lower():
            raise RuntimeError("HAProxy runtime rejected command")
        return result

def reachable(host,port,timeout=2):
    try:
        with socket.create_connection((host,int(port)),timeout=timeout): return True
    except (OSError,ValueError): return False

def save(obj):
    STATE.parent.mkdir(mode=0o750,parents=True,exist_ok=True)
    fd,path=tempfile.mkstemp(prefix=".ha-",dir=str(STATE.parent))
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as stream:
            json.dump(obj,stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(path,0o600)
        os.replace(path,STATE)
    finally:
        if os.path.exists(path): os.unlink(path)

def load():
    try: return json.loads(STATE.read_text(encoding="utf-8"))
    except FileNotFoundError: return {}

def services():
    data=json.loads(SPECS.read_text(encoding="utf-8"))
    if not isinstance(data,list): raise ValueError("Expected list of L4 services")
    for spec in data:
        nodes=spec.get("backends",[])
        if len(nodes)!=2 or nodes[0].get("standby") or not nodes[1].get("standby"):
            raise ValueError("HA requires primary node1 and backup node2")
    return data

def apply_once(items):
    states=load()
    for spec in items:
        name=spec["name"]
        primary=spec["backends"][0]
        healthy=reachable(primary["host"],primary["port"])
        status=states.get(name,"PRIMARY")
        if status=="STANDBY":
            command(f"disable server pkvm_{name}_nodes/node1")
        elif not healthy:
            command(f"disable server pkvm_{name}_nodes/node1")
            states[name]="STANDBY"
            save(states)
        # No automatic failback: a recovered primary remains disabled.
    return states

def failback(name):
    target=next((x for x in services() if x["name"]==name),None)
    if target is None: raise ValueError("Unknown HA service")
    primary=target["backends"][0]
    if not reachable(primary["host"],primary["port"]):
        raise RuntimeError("Primary TCP health check failed; failback refused")
    command(f"enable server pkvm_{name}_nodes/node1")
    current=load();current[name]="PRIMARY";save(current)

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--monitor",action="store_true")
    p.add_argument("--failback")
    args=p.parse_args()
    try:
        if args.failback: failback(args.failback)
        elif args.monitor:
            while True:
                try: apply_once(services())
                except (OSError,RuntimeError,ValueError) as exc:
                    print(f"HA monitor warning: {exc}",file=sys.stderr,flush=True)
                time.sleep(3)
        else: print(json.dumps(load(),ensure_ascii=False))
    except (OSError,ValueError,RuntimeError) as exc:
        print(f"HA operation failed: {exc}",file=sys.stderr)
        return 1
    return 0
if __name__=="__main__":
    sys.exit(main())
