import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("setup_split_dns", ROOT / "scripts/setup_split_dns.py")
split_dns = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(split_dns)


def test_views_follow_compute_domain_policy():
    rendered = split_dns.render(
        "wbl.int", "wbl.com", "60.10.10.in-addr.arpa",
        "192.168.0.0/24", "10.10.50.0/24", "10.10.60.0/24", "10.10.70.0/24",
        "10.10.60.1", ("192.168.0.40", "10.10.50.1", "10.10.60.1", "10.10.70.1"),
    )
    assert 'listen-on port 53 { 127.0.0.1; 192.168.0.40; 10.10.50.1; 10.10.60.1; 10.10.70.1; };' in rendered
    assert 'acl pkvm_public { 192.168.0.0/24; };' in rendered
    assert 'acl pkvm_private { 127.0.0.1; 10.10.50.0/24; 10.10.60.0/24; 10.10.70.0/24; };' in rendered
    assert 'zone "wbl.int" IN { type master; file "/var/named/pkvm/wbl.int.zone";' in rendered
    assert 'zone "wbl.com" IN { type master; file "/var/named/pkvm/external-internal.zone";' in rendered
    assert 'zone "wbl.com" IN { type forward; forward only;' in rendered
    assert 'zone "wbl.int" IN { type master; file "/var/named/pkvm/private-deny.zone";' in rendered
    assert 'view "private" {\n    match-clients { pkvm_private; };\n    recursion yes;' in rendered
