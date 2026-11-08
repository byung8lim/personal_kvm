import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("dns_endpoints", ROOT / "scripts" / "configure_dns_endpoints.py")
dns_endpoints = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dns_endpoints)


def test_each_network_uses_its_own_host_bridge_endpoint():
    data = {"networks": {
        "bridged": {"cidr": "192.168.0.0/24", "host_ip_cidr": "192.168.0.40/24"},
        "nat": {"cidr": "10.10.50.0/24", "gateway": "10.10.50.1"},
        "internal": {"cidr": "10.10.60.0/24", "gateway": None},
        "management": {"cidr": "10.10.70.0/24", "host_bind_ip": "10.10.70.1"},
    }}
    assert dns_endpoints.configure(data) == [
        "192.168.0.40", "10.10.50.1", "10.10.60.1", "10.10.70.1"
    ]
    assert [item["dns"] for item in data["networks"].values()] == [
        "192.168.0.40", "10.10.50.1", "10.10.60.1", "10.10.70.1"
    ]
