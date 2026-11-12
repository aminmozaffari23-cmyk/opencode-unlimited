#!/bin/bash
# Emulates "what address does the world see": only exists while the tunnel is up.
ST="${FAKE_WARP_STATE:?explicit fake state required}"
case "$ST" in "${OUC_TEST_SANDBOX:?explicit test root required}"/*) ;; *) exit 91 ;; esac
python3 - "$ST" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    d = {}
if d.get("connected") and d.get("registered"):
    print(f"2a09:bac5:b0{int(d.get('reg_count', 1)):02x}:1d37::1")
PY
