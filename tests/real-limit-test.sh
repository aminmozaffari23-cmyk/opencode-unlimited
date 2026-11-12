#!/usr/bin/env bash
# real-limit-test.sh — hit opencode's real free-usage wall and watch
# opencode-unlimited react, with numbers taken from the two logs rather than
# from guesswork.
#
# What it does, in order:
#   1. sends real prompts to a free model until opencode logs the wall
#   2. reports how long *the tool* took to notice it, and how long the rotation
#      took, straight from supervisor.log timestamps
#   3. says whether the egress /64 actually changed
#   4. sends one more prompt to prove opencode works again
#
# usage: bash tests/real-limit-test.sh [max-requests]
# env:   OUC_TEST_MODEL (default opencode/flint-40b-free)
set -u
MAX="${1:-120}"
MODEL="${OUC_TEST_MODEL:-opencode/flint-40b-free}"
LOG="${OUC_OPENCODE_LOG:-$HOME/.local/share/opencode/log/opencode.log}"
SUP="${OUC_SUPERVISOR_LOG:-/var/lib/opencode-unlimited/supervisor.log}"
STATE="${OUC_STATE_JSON:-/var/lib/opencode-unlimited/state.json}"
OUT="${OUC_TEST_OUT:-${TMPDIR:-/tmp}/real-limit-test.log}"
export PATH="$HOME/.opencode/bin:$PATH"

say()   { printf '%s\n' "$*" | tee -a "$OUT"; }
block() { curl -s -6 -m 10 https://api64.ipify.org | python3 -c "
import sys, ipaddress
a = sys.stdin.read().strip()
try: print(ipaddress.ip_network(a + '/64', strict=False))
except Exception: print('')"; }
sup()   { sudo grep -a -- "$1" "$SUP" 2>/dev/null | tail -1 | awk '{print $1}'; }
ts2s()  { date -d "$1" +%s 2>/dev/null || echo 0; }
state() { sudo python3 -c "
import json;d=json.load(open('$STATE'));print($1)" 2>/dev/null; }

: > "$OUT"
say "=== opencode-unlimited — real free-tier limit test ==="
say "started:  $(date -Is)"
say "model:    $MODEL"
say "opencode: $(opencode --version 2>/dev/null | tail -1)"

# a block that is already exhausted cannot demonstrate recovery — wait it out
if [ "$(state "d.get('pending_wall')")" = "True" ]; then
  say "this /64 is already exhausted; waiting for the supervisor to move us first"
  prev=$(block); w=0
  while [ "$w" -lt 150 ]; do
    sleep 3; w=$((w+3))
    cur=$(block); [ -n "$cur" ] && [ "$cur" != "$prev" ] && break
  done
fi

B0=$(block); WALLS0=$(grep -c "Free usage limit reached" "$LOG" 2>/dev/null || true)
WALLS0=${WALLS0:-0}
say "starting /64: ${B0:-<no ipv6>}"
say "wall lines already in opencode's log: $WALLS0"
say ""

n=0; wall_ts=""
while [ "$n" -lt "$MAX" ]; do
  n=$((n+1))
  t0=$(date +%s)
  timeout 300 opencode run -m "$MODEL" "Reply with the single word: ok" >/dev/null 2>&1
  took=$(( $(date +%s) - t0 ))
  walls_now=$(grep -c "Free usage limit reached" "$LOG" 2>/dev/null || true)
  walls_now=${walls_now:-0}
  if [ "$walls_now" -gt "$WALLS0" ]; then
    wall_ts=$(grep "Free usage limit reached" "$LOG" | grep -o 'timestamp=[^ ]*' | tail -1 \
              | cut -d= -f2 | tr -d Z)
    say "### WALL HIT on request #$n (opencode logged it at ${wall_ts}Z; the CLI call itself took ${took}s)"
    say "    message: $(grep -o 'error.error=\"AI_APICallError[^\"]*\"' "$LOG" | tail -1 | cut -c1-160)"
    break
  fi
  [ $((n % 10)) -eq 0 ] && say "  ... $n request(s), no wall yet (last took ${took}s)"
done

if [ -z "$wall_ts" ]; then
  say "no wall after $n request(s) — quota not exhausted; nothing to observe"
  exit 2
fi

# ---- what the tool did about it, from its own log -------------------------
det_ts=$(sup "quota wall detected")
rot_ts=$(sup "rotation done")
say ""
say "--- reaction (timestamps from supervisor.log) ---"
if [ -n "$det_ts" ]; then
  say "detected by the supervisor:  ${det_ts}Z  (+$(( $(ts2s "$det_ts") - $(ts2s "$wall_ts") ))s)"
else
  say "detected by the supervisor:  not yet"
fi
if [ -n "$rot_ts" ] && [ "$(ts2s "$rot_ts")" -ge "$(ts2s "$wall_ts")" ]; then
  say "rotation finished:           ${rot_ts}Z  (+$(( $(ts2s "$rot_ts") - $(ts2s "$wall_ts") ))s after the wall)"
else
  say "rotation finished:           not yet — waiting up to 150s"
  w=0; while [ "$w" -lt 150 ]; do
    sleep 5; w=$((w+5))
    rot_ts=$(sup "rotation done")
    [ -n "$rot_ts" ] && [ "$(ts2s "$rot_ts")" -ge "$(ts2s "$wall_ts")" ] && break
  done
  say "rotation finished:           ${rot_ts}Z  (+$(( $(ts2s "$rot_ts") - $(ts2s "$wall_ts") ))s)"
fi
say ""
say "supervisor log, that rotation:"
sudo grep -a -A9 "quota wall detected" "$SUP" | tail -10 | sed 's/^/    /' | tee -a "$OUT"

B1=$(block)
say ""
say "/64 before: ${B0:-?}"
say "/64 now:    ${B1:-?}"
say "changed:    $([ -n "$B1" ] && [ "$B1" != "$B0" ] && echo YES || echo NO)"
say "block reuse warning raised: $(state "bool(d.get('last_block_reused'))")"
say "blacklist: $(state "' '.join(d.get('blacklist') or [])")"

# ---- does opencode work again? --------------------------------------------
say ""
say "--- one more real request ---"
t0=$(date +%s)
out=$(timeout 300 opencode run -m "$MODEL" "Reply with exactly: RECOVERED-OK" 2>&1)
took=$(( $(date +%s) - t0 ))
if printf '%s' "$out" | grep -q "RECOVERED-OK"; then
  say "opencode answered on the current /64: YES (${took}s)"
else
  say "opencode answered on the current /64: NO (${took}s)"
  say "  (expected when Cloudflare has no fresh /64 left for this device today;"
  say "   the tool says so in its log instead of pretending otherwise)"
fi
say ""
say "=== end: $(date -Is) ==="
