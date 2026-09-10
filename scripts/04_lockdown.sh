#!/usr/bin/env bash
# Venue lockdown: block ALL outbound traffic except loopback.
# This is what turns the sovereignty claim into a demonstrable fact - after
# running it, /sovereignty/canary returns BLOCKED.
#
# Requires root. Run it at the venue, not on your dev box.
#   sudo ./scripts/04_lockdown.sh on     # enforce
#   sudo ./scripts/04_lockdown.sh off    # restore
#   ./scripts/04_lockdown.sh status
set -euo pipefail
TABLE=sovereign

case "${1:-status}" in
  on)
    nft list table inet $TABLE >/dev/null 2>&1 && nft delete table inet $TABLE
    nft -f - <<'RULES'
table inet sovereign {
  chain output {
    type filter hook output priority 0; policy drop;
    oif "lo" accept
    ip daddr 127.0.0.0/8 accept
    ip6 daddr ::1 accept
    ct state established,related accept
    counter comment "dropped-egress"
  }
}
RULES
    echo "egress BLOCKED (loopback only). Ollama on 127.0.0.1 still reachable."
    ;;
  off)
    nft delete table inet $TABLE 2>/dev/null || true
    echo "egress restored"
    ;;
  status)
    if nft list table inet $TABLE >/dev/null 2>&1; then
      echo "LOCKED DOWN"; nft list table inet $TABLE | grep -A2 counter || true
    else
      echo "not locked down (normal networking)"
    fi
    ;;
  *) echo "usage: $0 {on|off|status}"; exit 2 ;;
esac
