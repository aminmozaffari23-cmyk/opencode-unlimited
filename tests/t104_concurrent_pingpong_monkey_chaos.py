#!/usr/bin/env python3
"""Offline concurrent CLI contention against real locks and local stand-ins."""
import concurrent.futures
import fcntl
import json
import os
import unittest
from pingpong_fixture import Fixture


class Chaos(unittest.TestCase):
    def test_stampede_refused_hops_have_no_route_or_state_drift(self):
        with Fixture() as f:
            setup = f.command('hop', '--backend', 'psiphon')
            self.assertEqual(setup.returncode, 0, setup.stdout+setup.stderr)
            before = (f.state/'state.json').read_bytes()
            pidfile = f.state/'psiphon'/'psiphon.pid'
            pid = int(pidfile.read_text())
            with open(f.state/'rotation.lock', 'r') as lease:
                fcntl.flock(lease.fileno(), fcntl.LOCK_EX)
                commands = [('hop','--backend','warp'), ('hop','--backend','psiphon'),
                            ('status',), ('doctor',)] * 3
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(commands)) as pool:
                    results = list(pool.map(lambda args: f.command(*args, timeout=40), commands))
                for args, result in zip(commands, results):
                    self.assertNotIn('Traceback (most recent call last)', result.stdout+result.stderr)
                    if args[0] == 'hop':
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn('rotation already in progress', result.stdout+result.stderr)
                self.assertEqual((f.state/'state.json').read_bytes(), before)
                self.assertEqual(int(pidfile.read_text()), pid)
                self.assertTrue(__import__("pathlib").Path("/proc", str(pid)).exists())
                json.loads(before)
            result = f.command('status')
            self.assertNotIn('Traceback (most recent call last)', result.stdout+result.stderr)
            # Exact config-path ownership is the same criterion the controller uses.
            config = str(f.state/'psiphon'/'psiphon.config').encode()
            owners = []
            for proc in __import__('pathlib').Path('/proc').iterdir():
                if not proc.name.isdigit(): continue
                try: cmd = (proc/'cmdline').read_bytes()
                except OSError: continue
                if config in cmd and b'-config' in cmd: owners.append(int(proc.name))
            self.assertLessEqual(len(owners), 1, owners)

if __name__ == '__main__':
    unittest.main(verbosity=2)
