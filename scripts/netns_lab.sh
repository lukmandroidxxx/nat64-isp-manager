#!/usr/bin/env bash
# Build a disposable NAT64 laboratory using Linux network namespaces.
#
#   sudo ./scripts/netns_lab.sh up      # create gw/client/server namespaces
#   sudo ./scripts/netns_lab.sh status  # show topology and Jool pool4
#   sudo ./scripts/netns_lab.sh down    # remove the namespaces
#
# Topology:
#
#   client fd00:64::2/64 -- veth -- fd00:64::1/64 gw 198.51.100.1/24 -- veth -- 198.51.100.100/24 server
#        route 64:ff9b::/96 via fd00:64::1          pool4 203.0.113.1..32
#
# The script is destructive only inside the three namespaces it owns. It does
# not touch the host firewall, routing table or interfaces.
set -euo pipefail

GW_NS="${GW_NS:-nat64-gw}"
CLIENT_NS="${CLIENT_NS:-nat64-client}"
SERVER_NS="${SERVER_NS:-nat64-server}"
INSTANCE="${INSTANCE:-nat64test}"
POOL6="${POOL6:-64:ff9b::/96}"
POOL_BASE="${POOL_BASE:-203.0.113}"
POOL_COUNT="${POOL_COUNT:-32}"
SERVER_V4="${SERVER_V4:-198.51.100.100}"
GW_V4="${GW_V4:-198.51.100.1}"
PORT_MIN="${PORT_MIN:-61001}"
PORT_MAX="${PORT_MAX:-65535}"

need_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "Run as root: network namespaces and Jool require privileges." >&2
    exit 1
  fi
  if [[ "$(uname -s)" != "Linux" ]]; then
    echo "This lab requires Linux." >&2
    exit 1
  fi
  local missing=()
  for tool in ip jool ping sysctl; do
    command -v "${tool}" >/dev/null 2>&1 || missing+=("${tool}")
  done
  if (( ${#missing[@]} )); then
    echo "Missing required tools: ${missing[*]}" >&2
    echo "Install iproute2, Jool 4 (jool-tools/jool-dkms) and iputils-ping." >&2
    exit 1
  fi
}

cleanup() {
  for ns in "${CLIENT_NS}" "${SERVER_NS}" "${GW_NS}"; do
    ip netns del "${ns}" 2>/dev/null || true
  done
}

up() {
  need_root
  cleanup

  echo "==> Creating namespaces and veth pairs"
  ip netns add "${GW_NS}"
  ip netns add "${CLIENT_NS}"
  ip netns add "${SERVER_NS}"

  ip link add veth-client type veth peer name veth-gw-client
  ip link set veth-client netns "${CLIENT_NS}"
  ip link set veth-gw-client netns "${GW_NS}"

  ip link add veth-server type veth peer name veth-gw-server
  ip link set veth-server netns "${SERVER_NS}"
  ip link set veth-gw-server netns "${GW_NS}"

  for ns in "${GW_NS}" "${CLIENT_NS}" "${SERVER_NS}"; do
    ip -n "${ns}" link set lo up
  done

  echo "==> Assigning addresses"
  ip -n "${CLIENT_NS}" addr add fd00:64::2/64 dev veth-client
  ip -n "${CLIENT_NS}" link set veth-client up
  ip -n "${GW_NS}" addr add fd00:64::1/64 dev veth-gw-client
  ip -n "${GW_NS}" link set veth-gw-client up

  ip -n "${GW_NS}" addr add "${GW_V4}/24" dev veth-gw-server
  ip -n "${GW_NS}" link set veth-gw-server up
  ip -n "${SERVER_NS}" addr add "${SERVER_V4}/24" dev veth-server
  ip -n "${SERVER_NS}" link set veth-server up

  ip -n "${CLIENT_NS}" -6 route add "${POOL6}" via fd00:64::1
  ip -n "${SERVER_NS}" route add default via "${GW_V4}"

  echo "==> Enabling forwarding"
  ip netns exec "${GW_NS}" sysctl -q -w net.ipv4.ip_forward=1
  ip netns exec "${GW_NS}" sysctl -q -w net.ipv6.conf.all.forwarding=1

  echo "==> Starting Jool instance ${INSTANCE} (${POOL6})"
  modprobe jool
  ip netns exec "${GW_NS}" jool instance add "${INSTANCE}" --netfilter --pool6 "${POOL6}"
  ip netns exec "${GW_NS}" jool -i "${INSTANCE}" global update manually-enabled true
  ip netns exec "${GW_NS}" jool -i "${INSTANCE}" global update logging-session true

  echo "==> Adding ${POOL_COUNT} pool4 addresses (${POOL_BASE}.1-${POOL_COUNT})"
  for ((i = 1; i <= POOL_COUNT; i++)); do
    addr="${POOL_BASE}.${i}"
    ip -n "${GW_NS}" addr add "${addr}/32" dev veth-gw-server
    for proto in tcp udp icmp; do
      ip netns exec "${GW_NS}" jool -i "${INSTANCE}" pool4 add "--${proto}" "${addr}" \
        "${PORT_MIN}-${PORT_MAX}"
    done
  done

  echo
  echo "Lab ready."
  echo "  server IPv4: ${SERVER_V4}"
  echo "  NAT64 target from client: $(python3 - "${POOL6}" "${SERVER_V4}" <<'PY'
import ipaddress, sys
net = ipaddress.ip_network(sys.argv[1])
v4 = int(ipaddress.ip_address(sys.argv[2]))
print(ipaddress.ip_address(int(net.network_address) + v4))
PY
)"
  echo "  run a probe:  ip netns exec ${CLIENT_NS} ping -6 -c1 64:ff9b::c633:6464"
  echo "  remove lab:   $0 down"
}

status() {
  need_root
  echo "==> Namespaces"
  ip netns list | grep -E "(${GW_NS}|${CLIENT_NS}|${SERVER_NS})" || true
  echo "==> Gateway addresses"
  ip -n "${GW_NS}" -brief addr || true
  echo "==> pool4"
  for proto in tcp udp icmp; do
    echo "-- ${proto}"
    ip netns exec "${GW_NS}" jool -i "${INSTANCE}" pool4 display "--${proto}" || true
  done
  echo "==> sessions"
  ip netns exec "${GW_NS}" jool -i "${INSTANCE}" session display --numeric || true
}

case "${1:-}" in
  up) up ;;
  down) cleanup; echo "Lab removed." ;;
  status) status ;;
  *) echo "Usage: $0 {up|down|status}" >&2; exit 2 ;;
esac
