#!/bin/bash
# chaos.sh — inject the bad luck an hours-long run will meet, and measure it.
#
#   bash tests/chaos.sh            # every scenario, one sandbox each
#   bash tests/chaos.sh --soak 40  # 40 consecutive quota walls, one step
#
# This is not a second test suite: it is the "what does it cost to recover"
# measurement. Every scenario prints PASS/FAIL and the recovery time; the run
# ends with a table you can paste into TEST_RESULTS.md.
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
HELPER="$HERE/helpers"
SOAK=0
[ "${1:-}" = "--soak" ] && SOAK="${2:-20}"

chmod +x "$HELPER"/*.py "$HELPER"/*.sh 2>/dev/null || true
c_g=$'\033[32m'; c_r=$'\033[31m'; c_0=$'\033[0m'
declare -a NAMES RESULTS TIMES
FAILED=0

sandbox(){
  T="$(mktemp -d /tmp/ouc-chaos.XXXXXX)"
  export OUC_STATE_DIR="$T/state" OUC_TARGET_HOME="$T/home" OUC_PREFIX="$T/prefix"
  export OUC_UNIT_DIR="$T/units" OUC_BIN_PATH="$T/prefix/bin/ouc"
  export OUC_LIB_DIR="$T/prefix/lib/opencode-unlimited"
  export OUC_WARP_BIN="$HELPER/fake_warp.py" OUC_PROBE_CMD="$HELPER/fake_probe.sh"
  export OUC_IPTABLES="$HELPER/fake_iptables.py" OUC_IP6TABLES="$HELPER/fake_iptables.py"
  export FAKE_WARP_STATE="$T/warp.json" FAKE_WARP_LOG="$T/warp.log" FAKE_WARP_MIN_SETTLE=0
  export FAKE_AGENT_STATE="$T/agent.json" FAKE_OC_ARGS="$T/args.txt"
  export OUC_WARP_MODE=warp OUC_PIN=0 OUC_VERIFY=0
  # OUC_CHAOS_GAP=1 runs the soak with the production 15-20 s spacing
  if [ "${OUC_CHAOS_GAP:-0}" = 1 ]; then unset OUC_MIN_ROTATE_GAP OUC_ROTATE_GAP_JITTER 2>/dev/null; else export OUC_MIN_ROTATE_GAP=0; fi
  export OUC_CONNECT_WAIT=4 OUC_EGRESS_WAIT=4 OUC_SETTLE_SECONDS=8
  export OUC_RESUME_BACKOFF=1 OUC_RESUME_BUDGET=120 OUC_IGNORE_TUN_CHECK=1
  export OUC_INSTANCE_LOCK="$T/state/supervisor.lock" OUC_WATCHDOG=0 OUC_HOSTS_FILE="$T/hosts"
  export OUC_OC_BIN="$HELPER/fake_agent.py" OUC_AGENT_ROOT="$T/work"
  mkdir -p "$OUC_TARGET_HOME/.local/share/opencode/log" "$OUC_STATE_DIR" "$OUC_AGENT_ROOT"
  LOG="$OUC_TARGET_HOME/.local/share/opencode/log/opencode.log"; : > "$LOG"
  JOURNAL="$OUC_TARGET_HOME/.local/state/opencode-unlimited"
  mkdir -p "$JOURNAL"
  echo '{"connected":true,"registered":true,"reg_count":1,"deleted_at":0,"fail_new":0}' > "$FAKE_WARP_STATE"
  printf -- "- survive the night\n" > "$T/task.txt"
  ouc(){ python3 "$ROOT/ouc.py" "$@"; }
}

agent(){ # $1 = JSON script array, rest = extra args
  printf '{"calls":0,"script":%s,"log":"%s","session":"ses_chaos","model":"opencode/flint-40b-free"}\n' \
    "$1" "$LOG" > "$FAKE_AGENT_STATE"
  shift
  ouc agent --task "$T/task.txt" "$@" 2>&1
}

wall_count(){ python3 -c "import json,sys;print(int(json.load(open(sys.argv[1]))['budgets_used'].get('walls',0)))" "$JOURNAL/journal.json" 2>/dev/null || echo 0; }

record(){ # name result seconds
  NAMES+=("$1"); RESULTS+=("$2"); TIMES+=("$3")
  if [ "$2" = "PASS" ]; then printf '  %s✓%s %-42s %ss\n' "$c_g" "$c_0" "$1" "$3"
  else printf '  %s✗%s %-42s %ss  → %s\n' "$c_r" "$c_0" "$1" "$3" "$4"; FAILED=$((FAILED+1)); fi
}

t0=$(date +%s)
run_timed(){ local a=$1; shift; "$@" > "$T/out.log" 2>&1; echo $(( $(date +%s) - a )); }

# ---------------------------------------------------------------- scenarios
echo "chaos: each scenario is a fresh sandbox"; echo

sandbox
s=$t0; agent '["wall","wall","wall","ok"]' >/dev/null 2>&1
dt=$(( $(date +%s) - s ))
[ "$(wall_count)" -ge 3 ] && record "three quota walls in a row" PASS "$dt" \
  || record "three quota walls in a row" FAIL "$dt" "walls=$(wall_count)"
rm -rf "$T"

sandbox
ouc start --portable >/dev/null 2>&1; export OUC_TICK_SECONDS=60
pid1=$(cat "$OUC_STATE_DIR/supervisor.pid" 2>/dev/null)
s=$(date +%s); kill -9 "$pid1" 2>/dev/null; ouc watchdog >/dev/null 2>&1
pid2=$(cat "$OUC_STATE_DIR/supervisor.pid" 2>/dev/null)
dt=$(( $(date +%s) - s ))
{ [ -n "$pid2" ] && [ "$pid2" != "$pid1" ] && kill -0 "$pid2" 2>/dev/null; } \
  && record "supervisor killed with -9" PASS "$dt" \
  || record "supervisor killed with -9" FAIL "$dt" "pid1=$pid1 pid2=$pid2"
ouc stop >/dev/null 2>&1; rm -rf "$T"

sandbox
s=$(date +%s)
printf '{"calls":0,"script":["ok"],"log":"%s"}\n' "$LOG" > "$FAKE_AGENT_STATE"
ouc agent --task "$T/task.txt" >/dev/null 2>&1
printf 'not json at all {{{' > "$JOURNAL/journal.json"
printf '{{{broken' > "$OUC_STATE_DIR/state.json"
ouc status >/dev/null 2>&1
ouc agent --task "$T/task.txt" --fresh >/dev/null 2>&1
dt=$(( $(date +%s) - s ))
python3 -c "import json,sys;json.load(open(sys.argv[1]));json.load(open(sys.argv[2]))" \
  "$JOURNAL/journal.json" "$OUC_STATE_DIR/state.json" 2>/dev/null \
  && record "state.json + journal corrupted" PASS "$dt" \
  || record "state.json + journal corrupted" FAIL "$dt" "still unreadable"
rm -rf "$T"

sandbox
s=$(date +%s)
printf '{"calls":0,"script":["ok","wall"],"log":"%s"}\n' "$LOG" > "$FAKE_AGENT_STATE"
python3 - "$JOURNAL/journal.json" <<'PY'
import json, sys
json.dump({"schema": 3, "task_id": "abandoned", "steps": [
    {"id": 1, "title": "was in flight", "state": "running", "attempts": 1,
     "started": 1, "ended": 0, "note": ""}],
    "budgets_used": {}, "errors": [], "state": "running", "started": 1},
    open(sys.argv[1], "w"))
PY
ouc agent --task "$T/task.txt" >/dev/null 2>&1
dt=$(( $(date +%s) - s ))
[ "$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['steps'][0]['state'])" "$JOURNAL/journal.json" 2>/dev/null)" = "done" ] \
  && record "step abandoned mid-flight" PASS "$dt" \
  || record "step abandoned mid-flight" FAIL "$dt" "step not finished"
rm -rf "$T"

sandbox
s=$(date +%s); agent '["rate","ok"]' >/dev/null 2>&1
hops=$(python3 -c "import json,sys;print(int(json.load(open(sys.argv[1])).get('hops') or 0))" "$OUC_STATE_DIR/state.json" 2>/dev/null || echo 9)
dt=$(( $(date +%s) - s ))
[ "$hops" = "0" ] && record "rate limit (no rotation)" PASS "$dt" \
  || record "rate limit (no rotation)" FAIL "$dt" "hops=$hops"
rm -rf "$T"

sandbox
s=$(date +%s); agent '["network","ok"]' >/dev/null 2>&1
hops=$(python3 -c "import json,sys;print(int(json.load(open(sys.argv[1])).get('hops') or 0))" "$OUC_STATE_DIR/state.json" 2>/dev/null || echo 9)
dt=$(( $(date +%s) - s ))
[ "$hops" = "0" ] && record "network outage (no rotation)" PASS "$dt" \
  || record "network outage (no rotation)" FAIL "$dt" "hops=$hops"
rm -rf "$T"

sandbox
s=$(date +%s); out=$(agent '["stall","ok"]' 2>&1)
dt=$(( $(date +%s) - s ))
printf '%s' "$out" | grep -q "steps: 1/1 done" && record "model call goes silent" PASS "$dt" \
  || record "model call goes silent" FAIL "$dt" "not finished"
rm -rf "$T"

sandbox
s=$(date +%s); out=$(agent '["fatal","ok"]' 2>&1)
dt=$(( $(date +%s) - s ))
printf '%s' "$out" | grep -q "unrecoverable" && record "auth error stops honestly" PASS "$dt" \
  || record "auth error stops honestly" FAIL "$dt" "did not stop"
rm -rf "$T"

if [ "$SOAK" -gt 0 ]; then
  sandbox
  script=$(python3 -c "print('[' + ','.join(['\"wall\"']*$SOAK) + ',\"ok\"]')")
  s=$(date +%s); out=$(agent "$script" 2>&1); dt=$(( $(date +%s) - s ))
  w=$(wall_count)
  [ "$w" -ge "$SOAK" ] && record "$SOAK consecutive walls (soak)" PASS "$dt" \
    || record "$SOAK consecutive walls (soak)" FAIL "$dt" "walls=$w"
  rm -rf "$T"
fi

echo
echo "  scenario                                  result   recovery"
for i in "${!NAMES[@]}"; do
  printf '  %-41s %-8s %ss\n' "${NAMES[$i]}" "${RESULTS[$i]}" "${TIMES[$i]}"
done
echo
if [ "$FAILED" = 0 ]; then
  printf '  %sall %d scenarios recovered%s\n' "$c_g" "${#NAMES[@]}" "$c_0"
else
  printf '  %s%d scenario(s) did not recover%s\n' "$c_r" "$FAILED" "$c_0"
fi
exit $([ "$FAILED" = 0 ] && echo 0 || echo 1)
