#!/usr/bin/env bash
# ACT 2 fault: a node-level firewall rule that drops large packets from port 80.
# This lives on the NODE, not in any Kubernetes object - kubectl cannot see it.
# Run it INSIDE the kind node (e.g. `docker exec <node> bash`), as root.
set -euo pipefail
iptables -I FORWARD 1 -p tcp --sport 80 -m length --length 1400:65535 -j DROP
echo "injected. small responses pass; large responses are silently dropped."
echo "remove with: iptables -D FORWARD -p tcp --sport 80 -m length --length 1400:65535 -j DROP"
