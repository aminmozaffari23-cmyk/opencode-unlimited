#!/usr/bin/env python3
"""Guard-tripwire probe: does a *touch* trip it, and does real tampering?

Usage: guard_probe.py <path-to-ouc.py> <home-dir>
Prints one JSON object:
  {"touch_is_silent": bool, "append_is_caught": bool, "dir_touch_is_silent": bool}
"""
import importlib.util
import json
import os
import sys
from sandbox_scope import checked_path


def load(path):
    spec = importlib.util.spec_from_file_location("ouc", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main():
    ouc_path, home = sys.argv[1], checked_path(sys.argv[2])
    os.environ["HOME"] = home
    os.environ["OUC_USER_STATE_DIR"] = os.path.join(home, ".local/state/opencode-unlimited")
    os.environ["OUC_STATE_DIR"] = os.path.join(home, "state")
    os.makedirs(os.environ["OUC_STATE_DIR"], exist_ok=True)
    ssh = os.path.join(home, ".ssh")
    os.makedirs(ssh, exist_ok=True)
    keys = os.path.join(ssh, "authorized_keys")
    original = b"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITESTKEY user@example\n"
    with open(keys, "wb") as fh:
        fh.write(original)

    ouc = load(ouc_path)
    cfg = ouc.Config()

    # 1) an external SSH login: identical bytes rewritten, mtime bumped.
    #    The bump is forced to a clearly different second, because a bare
    #    os.utime() inside the same second is invisible to int(mtime) anyway.
    import time as _time

    d = ouc.protected_snapshot(cfg)
    with open(keys, "wb") as fh:  # rewrite the very same content
        fh.write(original)
    later = int(_time.time()) + 30
    os.utime(keys, (later, later))
    os.utime(ssh, (later, later))
    touch_violations = ouc.audit_step(cfg, d)
    touch_silent = touch_violations == []

    # 2) touching the directory only
    d2 = ouc.protected_snapshot(cfg)
    with open(os.path.join(ssh, "known_hosts"), "a") as fh:
        pass
    os.utime(ssh, None)
    # known_hosts appears -> entry set changed, so this legitimately trips; use
    # mtime-only on an existing entry instead
    os.remove(os.path.join(ssh, "known_hosts"))
    d3 = ouc.protected_snapshot(cfg)
    later2 = int(_time.time()) + 60
    os.utime(ssh, (later2, later2))
    os.utime(keys, (later2, later2))
    dir_touch_silent = ouc.audit_step(cfg, d3) == []

    # 3) real tampering: a key is appended
    d4 = ouc.protected_snapshot(cfg)
    with open(keys, "ab") as fh:
        fh.write(b"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEVIL intruder@evil\n")
    append_violations = ouc.audit_step(cfg, d4)
    append_caught = any("authorized_keys" in v for v in append_violations)

    print(json.dumps({
        "touch_is_silent": touch_silent,
        "dir_touch_is_silent": dir_touch_silent,
        "append_is_caught": append_caught,
        "touch_violations": touch_violations,
        "append_violations": append_violations,
    }))


if __name__ == "__main__":
    main()
