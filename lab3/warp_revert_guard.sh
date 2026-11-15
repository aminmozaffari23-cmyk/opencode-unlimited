#!/bin/bash
# Dead-man's switch for the tunnel-mode window.
#
# Entering `warp-cli mode warp` (a real TUN device) on a GitHub Codespace can
# sever the very tunnel the operator is connected through: the container's
# default route is replaced and the agent's control channel goes with it. The
# machine is then unreachable over ssh while still being reachable by the
# control plane.
#
# So the risky window is only ever entered with this script already running.
# It waits $1 seconds (default 420) and then, unconditionally and locally —
# no network needed, only the local warp-cli unix socket and the local
# iptables — puts the machine back exactly the way it was:
#
#   1. `ouc rescue`      : drop the leak guard, drop the pin, restore egress
#   2. warp-cli mode proxy : leave TUN, go back to SOCKS5 on 127.0.0.1:40000
#   3. warp-cli connect    : reconnect through the SOCKS5 path
#
# It also fires early if the phase writes its done-marker, so a successful run
# does not sit in TUN mode for the full window.
set -u
WINDOW="${1:-420}"
MARKER="${2:-/workspace/lab3/gap2-done}"
REPO=/workspaces/opencode-unlimited
LOG=/workspace/lab3/revert-guard.log

echo "$(date -u +%FT%TZ) guard armed for ${WINDOW}s" >>"$LOG"
for _ in $(seq 1 "$WINDOW"); do
  [ -e "$MARKER" ] && { echo "$(date -u +%FT%TZ) marker seen, reverting early" >>"$LOG"; break; }
  sleep 1
done

cd "$REPO" 2>/dev/null || cd /tmp
export OUC_TARGET_HOME=/home/codespace OUC_OC_BIN=/usr/local/bin/opencode
export OUC_WARP_MODE=warp
timeout 120 sudo env OUC_TARGET_HOME=/home/codespace OUC_WARP_MODE=warp \
    python3 ouc.py rescue >>"$LOG" 2>&1
echo "$(date -u +%FT%TZ) rescue done" >>"$LOG"

warp-cli --accept-tos disconnect >>"$LOG" 2>&1
warp-cli --accept-tos mode proxy >>"$LOG" 2>&1
warp-cli --accept-tos connect >>"$LOG" 2>&1
sleep 8
echo "$(date -u +%FT%TZ) warp mode=$(warp-cli --accept-tos settings 2>/dev/null | grep -i '^Mode' || echo unknown)" >>"$LOG"
echo "$(date -u +%FT%TZ) egress=$(curl -s --max-time 12 https://api64.ipify.org)" >>"$LOG"
echo "$(date -u +%FT%TZ) REVERTED" >>"$LOG"
