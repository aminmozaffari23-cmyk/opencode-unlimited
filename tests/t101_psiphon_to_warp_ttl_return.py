#!/usr/bin/env python3
"""Offline CLI TTL return and failed return bounce using fake local tunnels."""
import json
import time
import unittest
from pingpong_fixture import Fixture


class ReturnTest(unittest.TestCase):
    def test_expiry_returns_and_failed_return_bounces(self):
        with Fixture() as f:
            first = f.command('hop', '--backend', 'psiphon', '--wait', '60')
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            st = f.read_state()
            self.assertEqual(st['active_backend'], 'psiphon')
            st['fallback_activated_at'] = int(time.time()) - 10000
            f.write_state(st)
            returned = f.command('hop', '--wait', '60', extra={'OUC_BLACKLIST_TTL':'100'})
            self.assertEqual(returned.returncode, 0, returned.stdout + returned.stderr)
            self.assertEqual(f.read_state()['active_backend'], 'warp')
            self.assertFalse((f.state/'psiphon'/'psiphon.pid').exists())
            again = f.command('hop', '--backend', 'psiphon', '--wait', '60')
            self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
            st = f.read_state()
            st['fallback_activated_at'] = int(time.time()) - 10000
            f.write_state(st)
            bounced = f.command('hop', '--wait', '60', extra={'OUC_BLACKLIST_TTL':'100', 'FAKE_WARP_KEYS_REPEAT':'500'})
            self.assertEqual(bounced.returncode, 0, bounced.stdout + bounced.stderr)
            state = f.read_state()
            self.assertEqual(state['active_backend'], 'psiphon')
            self.assertGreaterEqual(state['fallback_activated_at'], int(time.time())-30)
            self.assertIn('.', state['current'])

if __name__ == '__main__':
    unittest.main(verbosity=2)
