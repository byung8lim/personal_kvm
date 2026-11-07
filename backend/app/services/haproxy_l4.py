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
    """Generate a single explicit IP:port listener and one backend pool."""
    if not isinstance(spec, dict):
        raise ValueError("Specification must be a mapping")
    identifier = spec.get("name")
    if not isinstance(identifier, str) or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{0,39}", identifier):
        raise ValueError("Invalid service name")
    ip = str(ipaddress.ip_address(spec.get("listen_ip")))
    if ip == "0.0.0.0" or ip == "::":
        raise ValueError("Wildcard listening requires explicit operator policy")
    port = _port(spec.get("listen_port"))
    sni_domain = spec.get("sni_domain")
    tls_sni = bool(spec.get("tls_sni", False))
    if sni_domain and not tls_sni:
        raise ValueError("Plain TCP cannot route by domain without tls_sni=True")
    if sni_domain:
        _host(sni_domain)
    algorithm = spec.get("algorithm", "roundrobin")
    if algorithm not in _ALGOS:
        raise ValueError("Unsupported algorithm")
    maxconn = spec.get("maxconn", 2000)
    if type(maxconn) is not int or not 1 <= maxconn <= 100000:
        raise ValueError("Invalid maxconn")
    connect_timeout_ms = spec.get("connect_timeout_ms", 5000)
    client_timeout_ms = spec.get("client_timeout_ms", 60000)
    server_timeout_ms = spec.get("server_timeout_ms", 60000)
    for t_val in (connect_timeout_ms, client_timeout_ms, server_timeout_ms):
        if type(t_val) is not int or not 500 <= t_val <= 3600000:
            raise ValueError("Invalid timeout value")
    nodes = spec.get("backends")
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 32:
        raise ValueError("1..32 backend nodes required")
    fail_threshold = spec.get("failover_threshold", 3)
    if type(fail_threshold) is not int or not 1 <= fail_threshold <= 20:
        raise ValueError("failover_threshold must be 1..20")
    lines = [
        f"frontend pkvm_{identifier}_in",
        "    mode tcp",
        f"    bind {ip}:{port}",
        f"    maxconn {maxconn}",
        f"    timeout client {client_timeout_ms}ms",
    ]
    if tls_sni and sni_domain:
        lines.append("    tcp-request inspect-delay 5s")
        lines.append("    tcp-request content accept if { req_ssl_hello_type 1 }")
        lines.append(f"    use_backend pkvm_{identifier}_nodes if {{ req.ssl_sni -i {sni_domain} }}")
    else:
        lines.append(f"    default_backend pkvm_{identifier}_nodes")
    lines.extend([
        "",
        f"backend pkvm_{identifier}_nodes",
        "    mode tcp",
        f"    balance {algorithm}",
        "    option tcp-check",
        f"    timeout connect {connect_timeout_ms}ms",
        f"    timeout server {server_timeout_ms}ms",
    ])
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
        lines.append(f"    server node{index + 1} {host}:{target_port} check inter 2s fall {fail_threshold} rise 2 weight {weight}{suffix}")
    return "\n".join(lines) + "\n"
