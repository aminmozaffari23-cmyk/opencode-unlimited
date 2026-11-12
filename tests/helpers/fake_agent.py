#!/usr/bin/env python3
"""Stands in for `opencode run …` in the long-run agent tests.

Scripted by $FAKE_AGENT_STATE (JSON):
  {"calls": 0,
   "script": ["wall","ok"],        # outcome per call; the last one repeats
   "session": "ses_abc123",
   "model": "opencode/flint-40b-free",
   "log": "/path/opencode.log",
   "stall_seconds": 6,             # for the "stall" outcome
   "lines_per_call": 1}            # log bytes, to exercise recycling

Every outcome is a *real* line in the log file the controller watches, so the
classifier is exercised exactly as it is in production.
"""
import json
import os
import sys
import time

STATE = os.environ.get("FAKE_AGENT_STATE", "/tmp/fake-agent.json")
ARGV = os.environ.get("FAKE_OC_ARGS", "/tmp/fake-agent-args.txt")
LOG = os.environ.get("OUC_LOG_PATH") or os.path.expanduser(
    "~/.local/share/opencode/log/opencode.log")


def log_path(st=None):
    """Where the controller is watching: state file wins, then env, then default."""
    return (st or {}).get("log") or LOG

WALL = ('timestamp=2026-11-12T00:00:00.000Z level=ERROR run=abc message="stream error" '
        'providerID={provider} modelID={model} session.id={session} small=false '
        'agent=build mode=primary error.error="AI_APICallError: Free usage limit '
        'reached. Free models work in OpenCode (https://opencode.ai)"')
RATE = ('timestamp=2026-11-12T00:00:00.000Z level=ERROR run=abc message="stream error" '
        'providerID={provider} modelID={model} session.id={session} '
        'error.error="AI_APICallError: 429 Too Many Requests"')
NETWORK = ('timestamp=2026-11-12T00:00:00.000Z level=ERROR run=abc '
           'message="fetch failed: ECONNREFUSED 127.0.0.1:8080" session.id={session}')
FATAL = ('timestamp=2026-11-12T00:00:00.000Z level=ERROR run=abc '
         'message="401 Unauthorized: invalid api key" session.id={session}')


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


def append(line, path=None):
    path = path or LOG
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def main():
    st = load()
    st["calls"] = int(st.get("calls", 0)) + 1
    script = st.get("script") or ["ok"]
    outcome = script[min(st["calls"] - 1, len(script) - 1)]
    try:
        with open(ARGV, "a") as fh:
            fh.write(json.dumps(sys.argv[1:]) + "\n")
    except OSError:
        pass

    session = st.get("session") or "ses_abc123"
    model_full = st.get("model") or "opencode/flint-40b-free"
    provider, _, model = model_full.partition("/")

    if outcome == "stall":
        save(st)
        time.sleep(int(st.get("stall_seconds", 6)))
        print("late reply")
        return 0

    lpath = log_path(st)
    for _ in range(int(st.get("lines_per_call", 1))):
        if outcome == "wall":
            append(WALL.format(provider=provider, model=model, session=session), lpath)
        elif outcome == "rate":
            append(RATE.format(provider=provider, model=model, session=session), lpath)
        elif outcome == "network":
            append(NETWORK.format(session=session), lpath)
        elif outcome == "fatal":
            append(FATAL.format(session=session), lpath)
        elif outcome == "exit":
            append(f'level=ERROR message="some unknown failure {time.time()}"', lpath)

    save(st)
    if outcome == "ok":
        print("REPLY-OK")
        return 0
    if outcome == "exit":
        return 5
    print(f"AI_APICallError: {outcome}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
