#!/usr/bin/env python3
"""Atomic, rollback-capable operator deployment of managed HAProxy TCP services.

Input: JSON array of validated service definitions. Runs as root on the KVM host.
Creates an independent pkvm-haproxy.service configuration, never overwrites the
host's existing /etc/haproxy/haproxy.cfg.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import json

from app.services.haproxy_l4 import render_tcp_service

CONFIG = Path("/etc/haproxy/pkvm-managed.cfg")
UNIT = Path("/etc/systemd/system/pkvm-haproxy.service")

def generate(specs):
    if not isinstance(specs, list) or not specs:
        raise ValueError("Nonempty list of L4 services required")
    names=set()
    binds=set()
    blocks=[]
    for spec in specs:
        if not isinstance(spec,dict) or spec.get("layer","L4") != "L4":
            raise ValueError("Only L4 definitions accepted")
        name=spec.get("name")
        if name in names:
            raise ValueError("Duplicate service name")
        names.add(name)
        bind=(spec.get("listen_ip"),spec.get("listen_port"))
        if bind in binds:
            raise ValueError("Duplicate listener IP and port")
        binds.add(bind)
        blocks.append(render_tcp_service(spec))
    return ("global\n"
            "    log stdout format raw local0\n"
            "    maxconn 20000\n"
            "defaults\n"
            "    log global\n"
            "    mode tcp\n"
            "    option tcplog\n"
            "    timeout connect 5s\n"
            "    timeout client 60s\n"
            "    timeout server 60s\n\n"
            + "\n".join(blocks))

def run(command):
    result=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=30)
    if result.returncode:
        raise RuntimeError("HAProxy command failed: "+str(command[0]))
    return result

def install(specs, destination=CONFIG):
    if os.geteuid()!=0:
        raise PermissionError("Root permissions required")
    if not shutil.which("haproxy"):
        raise RuntimeError("HAProxy binary missing")
    content=generate(specs)
    destination.parent.mkdir(parents=True,exist_ok=True)
    old=destination.read_bytes() if destination.exists() else None
    fd,temp=tempfile.mkstemp(prefix=".pkvm-haproxy-",dir=str(destination.parent))
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        run(["haproxy","-c","-f",temp])
        os.chmod(temp,0o644)
        os.replace(temp,destination)
        try:
            run(["systemctl","restart","pkvm-haproxy.service"])
        except Exception:
            # Revert the candidate configuration. Never replace a working config
            # with a syntax-invalid candidate or leave an irrecoverable state.
            if old is None:
                destination.unlink(missing_ok=True)
            else:
                destination.write_bytes(old)
                os.chmod(destination,0o644)
                try:
                    run(["systemctl","restart","pkvm-haproxy.service"])
                except Exception:
                    pass
            raise
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return destination

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("definitions",type=Path)
    args=parser.parse_args()
    try:
        with args.definitions.open(encoding="utf-8") as stream:
            specs=json.load(stream)
        target=install(specs)
    except (ValueError,PermissionError,RuntimeError,OSError,subprocess.TimeoutExpired) as exc:
        print(f"HAProxy deployment rejected: {exc}",file=sys.stderr)
        return 1
    print(f"HAProxy active configuration: {target}")
    return 0

if __name__=="__main__":
    sys.exit(main())
