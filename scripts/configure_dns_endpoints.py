#!/usr/bin/env python3
"""Assign each VM network the Bind9 address on its own host bridge."""
import argparse
import ipaddress
import os
from pathlib import Path
import tempfile
import yaml


def configure(data):
    networks = data["networks"]
    public_ip = str(ipaddress.ip_interface(networks["bridged"]["host_ip_cidr"]).ip)
    data.setdefault("dns", {})["client_server"] = public_ip
    listeners = []
    for name, network in networks.items():
        subnet = ipaddress.ip_network(network["cidr"], strict=True)
        if name == "bridged":
            resolver = public_ip
        else:
            resolver = str(network.get("gateway") or network.get("host_bind_ip") or next(subnet.hosts()))
        if ipaddress.ip_address(resolver) not in subnet:
            raise ValueError(f"{name} DNS endpoint {resolver} is outside {subnet}")
        network["dns"] = resolver
        if resolver not in listeners:
            listeners.append(resolver)
    return listeners


def update(path):
    path = Path(path)
    with path.open(encoding="utf-8") as stream:
        data = yaml.safe_load(stream) or {}
    listeners = configure(data)
    stat = path.stat()
    fd, temp = tempfile.mkstemp(dir=str(path.parent), prefix=".config-dns-endpoints-", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            yaml.safe_dump(data, stream, allow_unicode=True, sort_keys=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, stat.st_mode & 0o777)
        os.chown(temp, stat.st_uid, stat.st_gid)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return listeners


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("config")
    args = parser.parse_args()
    for listener in update(args.config):
        print(listener)


if __name__ == "__main__":
    main()
