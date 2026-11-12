#!/usr/bin/env python3
"""Small probes for the agent tests: budgets, journal states, the run lock.

Everything here imports the real ouc.py — no re-implementation, so a test can
never pass on a copy of the logic that differs from the shipped one.
"""
import importlib.util
import json
import os
import sys
import time


def load(path):
    spec = importlib.util.spec_from_file_location("ouc", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main():
    mode, ouc_path = sys.argv[1], sys.argv[2]
    m = load(ouc_path)
    cfg = m.Config()

    if mode == "budget-stop":
        # pretend the run started N seconds ago
        started = time.time() - float(sys.argv[3])
        journal = m.Journal(cfg)
        journal.open("step one", resume=False)
        budget = m.Budget(cfg, journal, started=started)
        for _ in range(int(sys.argv[4])):
            budget.note_fail()
        print("reason=" + (budget.stop_reason() or "none"))
        print(f"max_hours={cfg.max_hours} max_consec_fail={cfg.max_consec_fail} "
              f"max_walls={cfg.max_walls} max_rotations={cfg.max_rotations} "
              f"resume_max={cfg.resume_max}")
        return 0

    if mode == "journal-states":
        journal = m.Journal(cfg)
        journal.open("", resume=True)
        print(json.dumps({"states": [s["state"] for s in journal.steps()],
                          "reconciled": [e["class"] for e in journal.data.get("errors", [])]}))
        return 0

    if mode == "hold-lock":
        fh = m._agent_lock(cfg)
        print("locked" if fh else "busy")
        sys.stdout.flush()
        time.sleep(float(sys.argv[3]) if len(sys.argv) > 3 else 30)
        return 0

    if mode == "classify":                   # what does the taxonomy say?
        text = sys.argv[3].encode()
        got = m.classify_failure(text)
        print(json.dumps(got))
        return 0

    print("unknown mode", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
