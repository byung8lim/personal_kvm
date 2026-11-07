"""Validated HAProxy L4 TCP configuration generation (no deployment side effects)."""
import ipaddress
import re

_HOST = re.compile(r"^(?=.{1,253}$)[a-zA-Z0-9][a-zA-Z0-9.-]*$")
_ALGOS = {"roundrobin", "leastconn", "source"}

def _port(value):
    if type(value) is not int or not 1 <= value <= 65535:
        raise ValueError("Invalid TCP port")
    return value

def _host(value):
    if not isinstance(value, str) or not _HOST.fullmatch(value) or ".." in value or value.endswith(("-", ".")):
        raise ValueError("Invalid backend hostname")
    return value

def render_tcp_service(spec):
    """Generate a single explicit IP:port listener and one backend pool.

    A hostname on a plain TCP stream cannot be reliably identified without TLS SNI.
    """
    if not isinstance(spec, dict):
        raise ValueError("Specification must be a mapping")
    identifier = spec.get("name")
    if not isinstance(identifier, str) or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{0,39}", identifier):
        raise ValueError("Invalid service name")
    ip = str(ipaddress.ip_address(spec.get("listen_ip")))
    if ip == "0.0.0.0" or ip == "::":
        raise ValueError("Wildcard listening requires explicit operator policy")
    port = _port(spec.get("listen_port"))
    algorithm = spec.get("algorithm", "roundrobin")
    if algorithm not in _ALGOS:
        raise ValueError("Unsupported algorithm")
    nodes = spec.get("backends")
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 32:
        raise ValueError("1..32 backend nodes required")
    lines = [
        f"frontend pkvm_{identifier}_in",
        "    mode tcp",
        f"    bind {ip}:{port}",
        f"    default_backend pkvm_{identifier}_nodes",
        "",
        f"backend pkvm_{identifier}_nodes",
        "    mode tcp",
        f"    balance {algorithm}",
        "    option tcp-check",
    ]
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            raise ValueError("Invalid backend")
        host = _host(node.get("host"))
        target_port = _port(node.get("port"))
        weight = node.get("weight", 1)
        if type(weight) is not int or not 1 <= weight <= 256:
            raise ValueError("Invalid weight")
        standby = node.get("standby", False)
        if type(standby) is not bool:
            raise ValueError("Invalid standby")
        suffix = " backup" if standby else ""
        lines.append(f"    server node{index + 1} {host}:{target_port} check inter 2s fall 3 rise 2 weight {weight}{suffix}")
    return "\n".join(lines) + "\n"
