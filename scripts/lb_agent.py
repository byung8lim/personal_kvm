"""Privileged, loopback-only UNIX socket service for validated LB deployment.

The backend account has no sudo access. The root-owned agent accepts commands
only over a root:pkvm socket and uses fixed paths and validated specifications.
"""
import grp
import json
import os
from pathlib import Path
import socket
import socketserver
import subprocess
import sys
import tempfile

SOCKET=Path("/run/pkvm-lb-agent/control.sock")
MAX_REQUEST=256*1024

def handle(request):
    from app.services.haproxy_l4 import render_tcp_service
    from app.services.traefik_l7 import render_yaml
    action=request.get("action")
    if action=="apply_l4":
        specs=request.get("services")
        if not isinstance(specs,list) or not specs or len(specs)>64:
            raise ValueError("1..64 L4 services required")
        for spec in specs: render_tcp_service(spec)
        tool="/opt/personal-kvm/scripts/apply_haproxy_services.py"
        fd,path=tempfile.mkstemp(prefix=".lb-",suffix=".json",dir="/var/lib/personal-kvm")
        try:
            with os.fdopen(fd,"w",encoding="utf-8") as f:
                json.dump(specs,f)
            os.chmod(path,0o600)
            subprocess.run([sys.executable,tool,path],check=True,timeout=45,
                           stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        finally:
            os.unlink(path)
        return {"status":"applied"}
    if action=="apply_l7":
        spec=request.get("service")
        render_yaml(spec)
        fd,path=tempfile.mkstemp(prefix=".route-",suffix=".json",dir="/var/lib/personal-kvm")
        try:
            with os.fdopen(fd,"w",encoding="utf-8") as f: json.dump(spec,f)
            os.chmod(path,0o600)
            subprocess.run([sys.executable,"/opt/personal-kvm/scripts/apply_traefik_route.py",path],
                           check=True,timeout=30,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        finally:
            os.unlink(path)
        return {"status":"applied"}
    if action=="failback":
        name=request.get("name")
        if not isinstance(name,str) or not name.isascii() or not name.replace("-","").replace("_","").isalnum():
            raise ValueError("Invalid service name")
        subprocess.run([sys.executable,"/opt/personal-kvm/scripts/ha_manager.py","--failback",name],
                       check=True,timeout=20,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        return {"status":"primary_restored"}
    raise ValueError("Unknown LB action")

class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        raw=b""
        while len(raw)<=MAX_REQUEST:
            block=self.request.recv(8192)
            if not block or b"\n" in block:
                raw+=block
                break
            raw+=block
        if len(raw)>MAX_REQUEST:
            self.request.sendall(b'{"error":"Request too large"}\n')
            return
        try:
            result=handle(json.loads(raw.split(b"\n",1)[0]))
        except Exception as exc:
            # Do not return paths, passwords or subprocess output to clients.
            result={"error":type(exc).__name__}
        self.request.sendall((json.dumps(result)+"\n").encode())

def main():
    if os.geteuid()!=0: raise PermissionError("root required")
    SOCKET.parent.mkdir(mode=0o750,parents=True,exist_ok=True)
    if SOCKET.exists(): SOCKET.unlink()
    with socketserver.ThreadingUnixStreamServer(str(SOCKET),Handler) as server:
        os.chown(SOCKET,0,grp.getgrnam("pkvm").gr_gid)
        os.chmod(SOCKET,0o660)
        server.serve_forever()

if __name__=="__main__":
    main()
