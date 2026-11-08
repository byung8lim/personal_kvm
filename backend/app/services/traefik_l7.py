"""File-provider Traefik HTTP routing configuration with 4 algorithms, healthcheck, and request preservation."""
import re
import shutil
import subprocess
from pathlib import Path
import yaml

HOST = re.compile(r"^(?=.{1,253}$)[a-zA-Z0-9]+(?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?$")
NAME = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
ALLOWED_ALGORITHMS = {"wrr", "p2c", "hrw", "leasttime"}
MIN_STRATEGY_VERSION = (3, 4)


def verify_traefik_version(*, executor=subprocess.run):
    """Reject unverified/older Traefik installations before publishing routes."""
    binary = shutil.which("traefik")
    if not binary and Path("/usr/local/bin/traefik").is_file():
        binary = "/usr/local/bin/traefik"
    if not binary:
        raise RuntimeError("Traefik binary not found")
    result = executor([binary, "version"], text=True, capture_output=True,
                      timeout=8, check=False)
    if result.returncode:
        raise RuntimeError("Cannot determine Traefik version")
    match = re.search(r"(?im)^Version:\s*v?(\d+)\.(\d+)(?:\.\d+)?", result.stdout)
    if not match:
        raise RuntimeError("Unrecognized Traefik version output")
    version = tuple(map(int, match.groups()))
    if version < MIN_STRATEGY_VERSION:
        raise RuntimeError("Traefik >= 3.4 required for all supported L7 strategies")
    return version


def render_route(spec):
    if not isinstance(spec, dict):
        raise ValueError("Route must be a mapping")
    name = spec.get("name")
    domain = spec.get("domain")
    prefix = spec.get("path_prefix", "/")
    algorithm = spec.get("algorithm", "wrr")
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError("Invalid name")
    if not isinstance(domain, str) or not HOST.fullmatch(domain) or ".." in domain:
        raise ValueError("Invalid domain")
    if not isinstance(prefix, str) or not prefix.startswith("/") or any(ch in prefix for ch in ("\n", "\r", "`")):
        raise ValueError("Invalid path prefix")
    if algorithm not in ALLOWED_ALGORITHMS:
        raise ValueError(f"Unsupported L7 algorithm: {algorithm}")
    targets = spec.get("backends")
    if not isinstance(targets, list) or not 1 <= len(targets) <= 32:
        raise ValueError("1..32 backends required")
    servers = []
    for target in targets:
        if not isinstance(target, dict):
            raise ValueError("Backend must be an object")
        url = target.get("url")
        if not isinstance(url, str) or not re.fullmatch(r"https?://[a-zA-Z0-9.-]+:[0-9]{1,5}", url) or ".." in url:
            raise ValueError("Invalid backend HTTP URL")
        port = int(url.rsplit(":", 1)[1])
        if not 1 <= port <= 65535:
            raise ValueError("Invalid backend port")
        srv = {"url": url}
        if "weight" in target and target["weight"] is not None:
            weight = target["weight"]
            if type(weight) is not int or not 1 <= weight <= 256:
                raise ValueError("Invalid backend weight")
            srv["weight"] = weight
        servers.append(srv)
    tls = spec.get("tls", False)
    if type(tls) is not bool:
        raise ValueError("tls must be bool")
    rule = f"Host(`{domain}`) && PathPrefix(`{prefix}`)"
    lb_cfg = {"servers": servers, "passHostHeader": True}
    if algorithm != "wrr":
        lb_cfg["strategy"] = algorithm
    health_path = spec.get("healthcheck_path")
    if health_path:
        if not isinstance(health_path, str) or not health_path.startswith("/") or any(
            x in health_path for x in ("\n", "\r", "`", "?", "#")
        ):
            raise ValueError("Invalid healthcheck_path")
        interval = spec.get("healthcheck_interval", "10s")
        timeout = spec.get("healthcheck_timeout", "3s")
        if not all(isinstance(x, str) and re.fullmatch(r"[1-9]\d{0,3}(ms|s|m)", x)
                   for x in (interval, timeout)):
            raise ValueError("Invalid health check interval/timeout")
        lb_cfg["healthcheck"] = {
            "path": health_path,
            "interval": interval,
            "timeout": timeout,
        }
    return {
        "http": {
            "routers": {
                name: {
                    "rule": rule,
                    "entryPoints": ["websecure" if tls else "web"],
                    "service": name,
                    **({"tls": {}} if tls else {}),
                }
            },
            "services": {name: {"loadBalancer": lb_cfg}},
        }
    }


def render_yaml(spec):
    rendered = render_route(spec)
    text = yaml.safe_dump(rendered, sort_keys=False, allow_unicode=True)
    parsed = yaml.safe_load(text)
    if not isinstance(parsed, dict) or "http" not in parsed:
        raise ValueError("Generated Traefik YAML failed validation")
    return text
