#!/usr/bin/env python3
"""Render the evidence directory as a Markdown summary for a CI step.

Reads every JSON the live driver wrote and reports what each phase actually
recorded: a phase that failed says why; a phase that never ran produces no
row at all (the workflow lists the files it found).
"""
import json
import pathlib
import sys

EV = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "lab3/evidence")
def verdict(data):
    """Say what the file records, not whether I like it.

    Guessing polarity is how a report starts lying: `pending_wall: false` is
    the *good* outcome, and `bridge_listening: false` is what a correct
    teardown looks like. So this reports the driver's own explicit signals —
    `rc`, `ok`, `error` — and otherwise says how much was recorded.
    """
    if not isinstance(data, dict):
        return "recorded"
    if data.get("error"):
        return f"error: {str(data['error'])[:120]}"
    bits = []
    if "rc" in data:
        bits.append(f"rc={data['rc']}")
    if "ok" in data:
        bits.append(f"ok={data['ok']}")
    if data.get("steps"):
        failed = [s for s in data["steps"]
                  if isinstance(s, dict) and (s.get("rc") not in (None, 0)
                                              or s.get("ok") is False)]
        bits.append(f"{len(data['steps'])} steps"
                    + (f", {len(failed)} with a failed rc/ok" if failed else ""))
    if data.get("commands"):
        bad = [c for c in data["commands"] if isinstance(c, dict) and c.get("rc")]
        bits.append(f"{len(data['commands'])} commands"
                    + (f", {len(bad)} non-zero" if bad else ", all rc=0"))
    return "; ".join(bits) or "recorded"


def main():
    print("### live-verify\n")
    print("| file | verdict |")
    print("| --- | --- |")
    files = sorted(EV.glob("*.json")) if EV.is_dir() else []
    for f in files:
        try:
            data = json.loads(f.read_text())
        except Exception as exc:                      # noqa: BLE001 - report it
            print(f"| `{f.name}` | UNREADABLE: {exc} |")
            continue
        print(f"| `{f.name}` | {verdict(data)} |")
    if not files:
        print("| — | no evidence files were produced |")


if __name__ == "__main__":
    main()
