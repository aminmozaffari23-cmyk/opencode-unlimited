"""Ownership-scoped process leak check for the pytest session."""
import os
from pathlib import Path
import subprocess
import sys
import uuid


def pytest_sessionstart(session):
    # Preserve a shell suite's identifier when pytest runs inside that suite.
    session._ouc_run_id = os.environ.setdefault('OUC_TEST_RUN_ID', uuid.uuid4().hex)


def pytest_sessionfinish(session, exitstatus):
    helper = Path(__file__).resolve().parent / 'helpers' / 'orphan_scope.py'
    prefix = []
    if os.geteuid() != 0:
        try:
            probe = subprocess.run(['sudo', '-n', 'true'], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=5)
            if probe.returncode == 0:
                prefix = ['sudo', '-n']
        except (OSError, subprocess.TimeoutExpired):
            pass
    command = prefix + [sys.executable, str(helper), '--run-id', session._ouc_run_id]
    check = subprocess.run(command, capture_output=True, text=True)
    if check.returncode:
        sys.stderr.write('\npytest: owned sandbox processes survived teardown:\n' + check.stdout + check.stderr)
        subprocess.run(command + ['--terminate'], capture_output=True, text=True)
        session.exitstatus = 1
