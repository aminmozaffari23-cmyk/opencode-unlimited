#!/usr/bin/env python3
"""Find or terminate only controller/fake processes bearing this test-run ID.

Never selects by name alone: ownership requires an exact run identifier and an
explicit sandbox path. A caller must arrange permissions to inspect its test
processes (the shell harness runs as root when available).
"""
import argparse
import os
import signal
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPERS = ROOT / 'tests' / 'helpers'


def owned(run_id, sandbox=None):
    found = []
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            env = dict(x.split(b'=', 1) for x in (proc / 'environ').read_bytes().split(b'\0') if b'=' in x)
            cmd = (proc / 'cmdline').read_bytes().split(b'\0')
        except (OSError, ValueError):
            continue
        if env.get(b'OUC_TEST_RUN_ID', b'').decode(errors='replace') != run_id:
            continue
        root = env.get(b'OUC_TEST_SANDBOX', b'').decode(errors='replace')
        if not root.startswith(('/tmp/ouc-test.', '/tmp/ouc-v2-unit-')) or (sandbox and root != sandbox):
            continue
        args = [x.decode(errors='replace') for x in cmd if x]
        if not any(x == str(ROOT / 'ouc.py') or
                   (x.startswith((str(HELPERS) + '/', root + '/')) and Path(x).name.startswith('fake_'))
                   for x in args):
            continue
        found.append((int(proc.name), root, args))
    return found


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--sandbox')
    parser.add_argument('--terminate', action='store_true')
    args = parser.parse_args()
    if not args.run_id or len(args.run_id) < 16:
        parser.error('a unique run ID is required')
    found = owned(args.run_id, args.sandbox)
    for pid, root, cmd in found:
        print(f'owned sandbox process pid={pid} sandbox={root} argv={cmd}', flush=True)
    if args.terminate:
        for pid, _, _ in found:
            try: os.kill(pid, signal.SIGTERM)
            except ProcessLookupError: pass
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and owned(args.run_id, args.sandbox):
            time.sleep(.05)
        for pid, _, _ in owned(args.run_id, args.sandbox):
            try: os.kill(pid, signal.SIGKILL)
            except ProcessLookupError: pass
        return 1 if owned(args.run_id, args.sandbox) else 0
    return 1 if found else 0


if __name__ == '__main__':
    raise SystemExit(main())
