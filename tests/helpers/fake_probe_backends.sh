#!/bin/bash
# "What address does the world see", backend-aware.
#
# The controller asks the probe for the current egress without telling it which
# backend is active, so this helper reads the same state.json the bridge reads
# and answers in the family that backend really produces:
#
#   warp    -> the IPv6 /64 the fake WARP tunnel is on (same as fake_probe.sh)
#   psiphon -> the next IPv4 /32 from $FAKE_PSIPHON_STATE (a short list, so a
#              test can drive several region hops deterministically)
#
# Everything stays inside the sandbox.
set -u
ST="${FAKE_WARP_STATE:?explicit fake state required}"
SB="${OUC_TEST_SANDBOX:?explicit test root required}"
case "$ST" in "$SB"/*) ;; *) echo "escapes sandbox" >&2; exit 91 ;; esac

python3 - "$SB" <<'PY'
import json
import os
import sys

sandbox = sys.argv[1]


def inside(path: str) -> bool:
    return bool(path) and path.startswith(sandbox + "/")


state_dir = os.environ.get("OUC_STATE_DIR", "")
active = "warp"
try:
    with open(os.path.join(state_dir, "state.json")) as fh:
        active = (json.load(fh) or {}).get("active_backend") or "warp"
except Exception:
    active = "warp"

if active == "psiphon":
    path = os.environ.get("FAKE_PSIPHON_STATE", "")
    if not inside(path):
        sys.exit(92)
    try:
        with open(path) as fh:
            data = json.load(fh)
    except Exception:
        data = {}
    # A real tunnel's egress depends on the region it is using, so a test can
    # pin "region -> address" with "by_region". Without it we round-robin, which
    # is fine when a test only needs "the address changed".
    by_region = data.get("by_region") or {}
    if by_region:
        region = ""
        try:
            with open(os.path.join(state_dir, "psiphon", "psiphon.config")) as fh:
                region = (json.load(fh) or {}).get("EgressRegion") or ""
        except Exception:
            region = ""
        if region in by_region:
            print(by_region[region])
            sys.exit(0)
    ips = data.get("ips") or []
    if not ips:
        print("")
        sys.exit(0)
    index = int(data.get("index", 0))
    print(ips[index % len(ips)])   # round-robin: consecutive probes differ
    data["index"] = index + 1
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)
    sys.exit(0)

try:
    with open(os.environ["FAKE_WARP_STATE"]) as fh:
        warp = json.load(fh)
except Exception:
    warp = {}
if warp.get("connected") and warp.get("registered"):
    count = int(warp.get("reg_count", 1))
    print(f"2a09:bac5:b0{count:02x}:1d37::1")
PY
