#!/usr/bin/env bash
# Functional DNS probe from a temporary client attached to a KVM bridge.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo 'root required' >&2; exit 1; }
[[ $# -ge 4 && $# -le 5 ]] || { echo "usage: $0 <bridge> <client-cidr> <dns-ip> <fqdn> [gateway]" >&2; exit 1; }
BRIDGE="$1"; CLIENT_CIDR="$2"; DNS_IP="$3"; FQDN="$4"
GATEWAY="${5:-}"
NS="pkvm-dns-test"; HOST_IF="pkvm-dns-host"; NS_IF="pkvm-dns-client"
cleanup() { ip netns del "$NS" >/dev/null 2>&1 || true; ip link del "$HOST_IF" >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup
ip link show "$BRIDGE" >/dev/null
ip netns add "$NS"
ip link add "$HOST_IF" type veth peer name "$NS_IF"
ip link set "$HOST_IF" master "$BRIDGE"
ip link set "$HOST_IF" up
ip link set "$NS_IF" netns "$NS"
ip netns exec "$NS" ip address add "$CLIENT_CIDR" dev "$NS_IF"
ip netns exec "$NS" ip link set lo up
ip netns exec "$NS" ip link set "$NS_IF" up
if [[ -n "$GATEWAY" ]]; then ip netns exec "$NS" ip route add default via "$GATEWAY"; fi
managed="$(ip netns exec "$NS" dig +short +time=2 +tries=1 @"$DNS_IP" "$FQDN" A)"
recursive="$(ip netns exec "$NS" dig +short +time=2 +tries=1 @"$DNS_IP" example.com A | head -n 1)"
[[ -n "$managed" ]] || { echo "managed lookup failed: $FQDN" >&2; exit 1; }
[[ -n "$recursive" ]] || { echo 'recursive lookup failed: example.com' >&2; exit 1; }
printf 'managed=%s\nrecursive=%s\n' "$managed" "$recursive"
