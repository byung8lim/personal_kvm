from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALL = (ROOT / "scripts/install.sh").read_text(encoding="utf-8")
NETWORK = (ROOT / "scripts/setup_networks.sh").read_text(encoding="utf-8")

def test_network_checkpoint_default_and_manual_override():
    assert 'export PKVM_ALLOW_NONINTERACTIVE_NETWORK="${PKVM_ALLOW_NONINTERACTIVE_NETWORK:-1}"' in INSTALL
    assert 'noninteractive_network_allowed() { [[ "${PKVM_ALLOW_NONINTERACTIVE_NETWORK:-0}" == 1 ]]; }' in NETWORK
    assert 'printf \'Yes\\n\' | "${CHECKPOINT_COMMAND[@]}"' in NETWORK
    assert 'nmcli device checkpoint --timeout 180 --' in NETWORK
    assert "die '브리지 변경이 실패하거나 체크포인트가 롤백되었습니다.'" in NETWORK

def test_dns_client_before_nameserver_and_network_contract():
    assert INSTALL.index('DNS_CLIENT_SERVER="${PKVM_DNS_CLIENT_IP:-${DNS_CONFIG[4]}}"') < INSTALL.index('DNS_ZONE_NS="${PKVM_DNS_SERVER_IP:-${DNS_CLIENT_SERVER:-${DNS_CONFIG[3]}}}"')
    assert 'host.ip not in public' in INSTALL

def test_install_serialization():
    assert 'flock -n 9' in INSTALL
