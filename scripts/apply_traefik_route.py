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
from app.services.traefik_l7 import render_yaml

ROOT = Path("/etc/personal-kvm/traefik/dynamic")
NAME = re.compile(r"^[a-z][a-z0-9-]{0,39}$")

def publish(spec, root=ROOT):
    name = spec.get("name")
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError("Invalid route name")
    content = render_yaml(spec)
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError("Traefik dynamic directory is missing or unsafe")
    path = root / (name + ".yaml")
    if path.is_symlink():
        raise RuntimeError("Refusing to replace symlink")
    fd, tmp = tempfile.mkstemp(dir=str(root), prefix=".route-", suffix=".yaml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as writer:
            writer.write(content)
            writer.flush()
            os.fsync(writer.fileno())
        if os.geteuid() == 0:
            os.chown(tmp, 0, grp.getgrnam("pkvm-traefik").gr_gid)
        os.chmod(tmp, 0o640)
        os.replace(tmp, path)
        directory_fd = os.open(root, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
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
