#!/usr/bin/env python3
"""Operator CLI: atomically publish or remove one validated Traefik file-provider route.

Run with the deployed backend virtualenv Python as root:
PYTHONPATH=/opt/personal-kvm/backend /opt/personal-kvm/backend/venv/bin/python \
    /opt/personal-kvm/scripts/apply_traefik_route.py /path/to/route.json
"""
import argparse
import grp
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import subprocess
import yaml
from app.services.traefik_l7 import render_yaml, verify_traefik_version

ROOT = Path("/etc/personal-kvm/traefik/dynamic")
NGINX_ROOT = Path("/etc/nginx/conf.d")
STATIC_CONFIG = Path("/etc/personal-kvm/traefik/traefik.yaml")
NAME = re.compile(r"^[a-z][a-z0-9-]{0,39}$")

def _entrypoint_ports(path=STATIC_CONFIG):
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    points = config.get("entryPoints", {})
    result = {}
    for name in ("web", "websecure"):
        address = points.get(name, {}).get("address")
        match = re.fullmatch(r"(?::|127\.0\.0\.1:)([0-9]{1,5})", str(address))
        if not match or not 1 <= int(match.group(1)) <= 65535:
            raise RuntimeError(f"Invalid Traefik {name} entry point")
        result[name] = int(match.group(1))
    return result

def render_nginx_ingress(spec, ports):
    domain = spec["domain"]
    tls = spec.get("tls", False)
    upstream_port = ports["websecure" if tls else "web"]
    scheme = "https" if tls else "http"
    tls_lines = ""
    if tls:
        tls_lines = ("    ssl_certificate /etc/personal-kvm/tls/server.crt;\n"
                     "    ssl_certificate_key /etc/personal-kvm/tls/server.key;\n"
                     "    ssl_protocols TLSv1.2 TLSv1.3;\n")
    return ("server {\n"
            f"    listen 0.0.0.0:{443 if tls else 80}{' ssl' if tls else ''};\n"
            f"    server_name {domain};\n"
            f"{tls_lines}"
            "    location / {\n"
            f"        proxy_pass {scheme}://127.0.0.1:{upstream_port};\n"
            "        proxy_http_version 1.1;\n"
            "        proxy_set_header Host $host;\n"
            "        proxy_set_header X-Real-IP $remote_addr;\n"
            "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
            "        proxy_set_header X-Forwarded-Proto $scheme;\n"
            "        proxy_ssl_server_name on;\n"
            "        proxy_ssl_name $host;\n"
            "        proxy_ssl_verify off;\n"
            "    }\n"
            "}\n")

def _replace(path, content, mode=0o640, group=None):
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".pkvm-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as writer:
            writer.write(content)
            writer.flush()
            os.fsync(writer.fileno())
        if os.geteuid() == 0 and group is not None:
            os.chown(tmp, 0, group)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def publish(spec, root=ROOT, *, nginx_root=NGINX_ROOT, static_config=STATIC_CONFIG,
            verify_binary=True, reload_nginx=True):
    name = spec.get("name")
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError("Invalid route name")
    content = render_yaml(spec)
    if verify_binary:
        verify_traefik_version()
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError("Traefik dynamic directory is missing or unsafe")
    path = root / (name + ".yaml")
    if path.is_symlink():
        raise RuntimeError("Refusing to replace symlink")
    ingress = None
    ingress_content = None
    if nginx_root is not None:
        if nginx_root.is_symlink() or not nginx_root.is_dir():
            raise RuntimeError("Nginx configuration directory is missing or unsafe")
        ingress = nginx_root / ("pkvm-l7-" + name + ".conf")
        if ingress.is_symlink():
            raise RuntimeError("Refusing to replace Nginx symlink")
        ingress_content = render_nginx_ingress(spec, _entrypoint_ports(static_config))
    old_route = path.read_bytes() if path.exists() else None
    old_ingress = ingress.read_bytes() if ingress and ingress.exists() else None
    try:
        _replace(path, content, group=grp.getgrnam("pkvm-traefik").gr_gid if os.geteuid() == 0 else None)
        if ingress:
            _replace(ingress, ingress_content, mode=0o644)
            if reload_nginx:
                subprocess.run(["nginx", "-t"], check=True, timeout=15,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run(["systemctl", "reload", "nginx.service"], check=True, timeout=15,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        if old_route is None:
            path.unlink(missing_ok=True)
        else:
            _replace(path, old_route.decode("utf-8"), group=grp.getgrnam("pkvm-traefik").gr_gid if os.geteuid() == 0 else None)
        if ingress:
            if old_ingress is None:
                ingress.unlink(missing_ok=True)
            else:
                _replace(ingress, old_ingress.decode("utf-8"), mode=0o644)
        raise
    return path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("spec_file", type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Run as root to publish into the controlled Traefik directory")
    with args.spec_file.open(encoding="utf-8") as reader:
        spec = json.load(reader)
    try:
        path = publish(spec)
    except (ValueError, RuntimeError, KeyError, OSError) as exc:
        print(f"Route was not published: {exc}", file=sys.stderr)
        return 1
    print(f"Published route: {path}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
