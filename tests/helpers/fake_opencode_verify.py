#!/usr/bin/env python3
"""Stands in for the opencode CLI during verification requests.

Behaviour is scripted by $FAKE_OC_STATE (JSON):
  {"fail_first": N, "log": "/path/opencode.log", "wall_text": "..."}
The first N verification calls append a quota-wall line to the log (i.e. the
block we were handed is already exhausted); after that they print the marker.
"""
import json, os, sys, time

STATE = os.environ.get("FAKE_OC_STATE", "/tmp/fake-oc-state.json")
MARKER = "OUC-VERIFY-OK"
WALL = ('timestamp=2026-11-08T00:00:00.000Z level=ERROR message="stream error" '
        'error.error="AI_APICallError: Free usage limit reached. Free models work '
        'in OpenCode (https://opencode.ai)"')

def load():
    try:
        with open(STATE) as fh:
            return json.load(fh)
    except Exception:
        return {"fail_first": 0, "calls": 0}

def save(d):
    with open(STATE + ".tmp", "w") as fh:
        json.dump(d, fh)
    os.replace(STATE + ".tmp", STATE)

if len(sys.argv) > 1 and sys.argv[1] == "run":
    if os.environ.get("FAKE_OC_REQUIRE_PROXY"):
        env = os.environ
        got = env.get("HTTPS_PROXY") or env.get("https_proxy") or ""
        st0 = load()
        st0["proxy_seen"] = got
        st0["no_proxy_seen"] = env.get("NO_PROXY") or env.get("no_proxy") or ""
        save(st0)
        if not got:
            print("no proxy in the environment", file=sys.stderr)
            sys.exit(3)
    st = load()
    st["calls"] = int(st.get("calls", 0)) + 1
    fail = int(st.get("fail_first", 0))
    if st["calls"] <= fail:
        path = (st.get("log") or os.environ.get("OUC_OPENCODE_LOG")
                or (os.path.join(os.environ["OUC_TARGET_HOME"],
                                 ".local/share/opencode/log/opencode.log")
                    if os.environ.get("OUC_TARGET_HOME") else "")
                or os.path.expanduser("~/.local/share/opencode/log/opencode.log"))
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a") as fh:
                fh.write(WALL + "\n")
        except OSError:
            pass
        save(st)
        print('AI_APICallError: Free usage limit reached.', file=sys.stderr)
        sys.exit(1)
    save(st)
    print(f"OK\n{MARKER}")
    sys.exit(0)

print("fake opencode (verification stand-in)")
