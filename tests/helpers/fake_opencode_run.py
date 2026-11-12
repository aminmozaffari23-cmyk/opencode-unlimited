#!/usr/bin/env python3
"""Stands in for the interactive `opencode run …` in the auto-resume tests.

Scripted by $FAKE_OC_STATE (JSON):
  {"calls": 0,
   "wall_first": 1,          # this many first calls hit the free-limit wall
   "wall_always": false,     # or: every call walls (to test the give-up path)
   "session": "ses_abc123",  # session id written into the wall line
   "model": "opencode/deepseek-v4.1-flash-free",
   "log": "/path/opencode.log"}

Each call appends its argv (one JSON line) to $FAKE_OC_ARGS, so a test can
assert exactly *how* the resume was asked for — `-s <session>` versus `-c`,
and which model and prompt were passed.
"""
import json
import os
import sys

STATE = os.environ.get("FAKE_OC_STATE", "/tmp/fake-oc-run.json")
ARGV = os.environ.get("FAKE_OC_ARGS", "/tmp/fake-oc-run-args.txt")

WALL = ('timestamp=2026-11-09T00:00:00.000Z level=ERROR run=abc message="stream error" '
        'providerID={provider} modelID={model} session.id={session} small=false '
        'agent=build mode=primary error.error="AI_APICallError: Free usage limit '
        'reached. Free models work in OpenCode (https://opencode.ai)"')


def load():
    try:
        with open(STATE) as fh:
            return json.load(fh)
    except Exception:
        return {}


def save(d):
    with open(STATE + ".tmp", "w") as fh:
        json.dump(d, fh)
    os.replace(STATE + ".tmp", STATE)


def main():
    argv = sys.argv[1:]
    st = load()
    st["calls"] = int(st.get("calls", 0)) + 1
    if argv and argv[0] == "run":
        try:
            with open(ARGV, "a") as fh:
                fh.write(json.dumps(argv) + "\n")
        except OSError:
            pass
    wall_always = bool(st.get("wall_always"))
    wall_first = int(st.get("wall_first", 0))
    should_wall = wall_always or st["calls"] <= wall_first

    if should_wall:
        session = st.get("session") or "ses_abc123"
        model_full = st.get("model") or "opencode/deepseek-v4.1-flash-free"
        provider, _, model = model_full.partition("/")
        path = (st.get("log") or os.environ.get("OUC_LOG_PATH")
                or os.path.expanduser("~/.local/share/opencode/log/opencode.log"))
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a") as fh:
                fh.write(WALL.format(provider=provider, model=model,
                                     session=session) + "\n")
        except OSError:
            pass
        save(st)
        print("AI_APICallError: Free usage limit reached.", file=sys.stderr)
        return 1

    save(st)
    print("REPLY-OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
