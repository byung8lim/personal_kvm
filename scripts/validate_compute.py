#!/usr/bin/env python3
"""Read-only preflight for compute.yaml; emits shell assignments only on success."""
import argparse
import ipaddress
import re
import shlex
import sys
from pathlib import Path

import yaml

DNS_LABEL = re.compile(r"^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$")
SERVICES = ("bind9", "nfs", "traefik", "haproxy")

def require_mapping(data, name):
    if not isinstance(data, dict):
        raise ValueError(f"{name}: mapping required")
    return data

def name(value, key):
    if not isinstance(value, str) or not value or len(value) > 253 or not all(
        DNS_LABEL.fullmatch(label) for label in value.strip(".").split(".")
    ):
        raise ValueError(f"{key}: invalid DNS domain")
    return value.rstrip(".").lower()

def validate(data):
    cfg = require_mapping(data, "compute")
    if cfg.get("schema_version") != 1:
        raise ValueError("schema_version must be 1")
    service = require_mapping(cfg.get("services"), "services")
    if set(service) != set(SERVICES):
        raise ValueError("services must define exactly bind9,nfs,traefik,haproxy")
    if any(type(v) is not bool for v in service.values()):
        raise ValueError("services entries must be booleans")
    network = require_mapping(cfg.get("network"), "network")
    subnets = {}
    for key in ("public", "internal", "management"):
        subnets[key] = ipaddress.ip_network(network[key], strict=True)
        if subnets[key].version != 4:
            raise ValueError(f"network.{key}: IPv4 required")
    for i, a in enumerate(subnets):
        for b in list(subnets)[i+1:]:
            if subnets[a].overlaps(subnets[b]):
                raise ValueError(f"network: {a}/{b} overlap")
    domains = require_mapping(cfg.get("domains"), "domains")
    external = domains.get("EXTERNAL_DOMAIN", domains.get("EXTERNAL_DOMIAN"))
    if domains.get("EXTERNAL_DOMAIN") and domains.get("EXTERNAL_DOMIAN") and domains["EXTERNAL_DOMAIN"] != domains["EXTERNAL_DOMIAN"]:
        raise ValueError("EXTERNAL_DOMIAN alias conflicts with EXTERNAL_DOMAIN")
    private = domains.get("PRIVATE_DOMAIN")
    if service["bind9"]:
        external = name(external, "EXTERNAL_DOMAIN")
        private = name(private, "PRIVATE_DOMAIN")
        if external == private:
            raise ValueError("external/private DNS domains must differ")
    bind9 = require_mapping(cfg.get("bind9", {}), "bind9")
    if service["bind9"]:
        ip = ipaddress.ip_address(bind9.get("server_ip"))
        if ip.version != 4 or ip not in subnets["internal"]:
            raise ValueError("bind9.server_ip must be within network.internal")
        reverse = name(bind9.get("reverse_zone"), "bind9.reverse_zone")
        if not reverse.endswith(".in-addr.arpa"):
            raise ValueError("bind9.reverse_zone must be in-addr.arpa")
    if service["nfs"]:
        nfs = require_mapping(cfg.get("nfs"), "nfs")
        path = nfs.get("NFS_DIR")
        if not isinstance(path, str) or not Path(path).is_absolute() or Path(path).resolve() in (Path("/"), Path("/etc"), Path("/var")):
            raise ValueError("nfs.NFS_DIR must be a dedicated absolute directory")
    for key in ("traefik", "haproxy"):
        if service[key]:
            cfg_service = require_mapping(cfg.get(key), key)
            port = cfg_service.get("listen_port")
            if type(port) is not int or not 1 <= port <= 65535:
                raise ValueError(f"{key}.listen_port must be 1..65535")
    reserved = {22, 53, 80, 443, 8000, 8080, 3306}
    for key in ("traefik", "haproxy"):
        if service[key] and cfg[key]["listen_port"] in reserved:
            raise ValueError(f"{key}.listen_port conflicts with a reserved system service")
    if service["traefik"]:
        p = cfg["traefik"]["listen_port"]
        if p >= 65535 or p+1 in reserved:
            raise ValueError("traefik requires an available adjacent HTTP port")
    if service["traefik"] and service["haproxy"] and cfg["haproxy"]["listen_port"] in (
        cfg["traefik"]["listen_port"], cfg["traefik"]["listen_port"]+1
    ):
        raise ValueError("traefik/haproxy listener ports conflict")
    values = {"PKVM_SETUP_BIND9": int(service["bind9"]),
              "PKVM_INTERNAL_CIDR": str(subnets["internal"]),
              "PKVM_MGMT_CIDR": str(subnets["management"])}
    if service["bind9"]:
        values["PKVM_EXTERNAL_DOMAIN"] = external
        values.update(PKVM_DNS_FORWARD_ZONE=private,
                      PKVM_DNS_REVERSE_ZONE=reverse,
                      PKVM_DNS_SERVER_IP=str(ip))
    values.update(PKVM_SETUP_NFS=int(service["nfs"]),
                  PKVM_SETUP_HAPROXY=int(service["haproxy"]),
                  PKVM_SETUP_TRAEFIK=int(service["traefik"]))
    if service["nfs"]:
        values["PKVM_NFS_DIR"] = cfg["nfs"]["NFS_DIR"]
        values["PKVM_MGMT_CIDR"] = str(subnets["management"])
    if service["haproxy"]:
        values["PKVM_HAPROXY_PORT"] = str(cfg["haproxy"]["listen_port"])
    if service["traefik"]:
        values["PKVM_TRAEFIK_PORT"] = str(cfg["traefik"]["listen_port"])
    return values

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    args = parser.parse_args()
    try:
        with args.config.open(encoding="utf-8") as stream:
            config = yaml.safe_load(stream)
        values = validate(config)
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        print(f"[compute.yaml 오류] {exc}", file=sys.stderr)
        return 2
    for key, value in values.items():
        print(f"export {key}={shlex.quote(str(value))}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
