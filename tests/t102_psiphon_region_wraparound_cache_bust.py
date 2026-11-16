#!/usr/bin/env python3
"""Offline simulated two-region wrap, using the real rotation and cache code.

No real tunnel or provider network is used. This is not live evidence.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_return_lifecycle import ouc, FakeState, CacheTransaction


class Wraparound(unittest.TestCase):
    def test_two_regions_then_duplicate_bust_and_fresh_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = SimpleNamespace(rotation_lock=str(Path(tmp) / 'rotation.lock'),
                psiphon_regions=['US', 'DE'], psiphon_start_wait=1, rotation_timeout=5,
                psiphon_socks_port=9060, proxy_http_port=8080, pin=False,
                proxy_mode=True, kill_switch=False)
            state = FakeState(active_backend='psiphon', fallback_activated_at=1,
                              psiphon_region_index=0, psiphon_cycle_visited=[],
                              psiphon_cycle_count=0)
            regions = []
            class Tunnel:
                def __init__(self, *args): pass
                def available(self): return True, 'fake'
                def start(self, region, **kw):
                    regions.append(region)
                    return True, 'connected'
                def rebootstrap(self, region):
                    regions.append('clean:' + region)
                    return True, 'connected'
                def pid(self): return 0
            # For each hop: old egress, then bridge candidate; third hop sees a
            # spent US exit and must re-probe after the clean restart.
            probes = iter(['2001:db8::1', '192.0.2.1',
                           '192.0.2.1', '192.0.2.2',
                           '192.0.2.2', '192.0.2.1', '192.0.2.3'])
            def spent(cfg, state):
                return set(state.load().get('blacklist', []))
            def blacklist(cfg, state, old):
                return list(set(state.load().get('blacklist', [])) | {old}), {}
            with patch.object(ouc, 'Psiphon', Tunnel), \
                 patch.object(ouc, '_wait_rotation_spacing'), \
                 patch.object(ouc, 'probe_egress', side_effect=lambda *a, **k: next(probes)), \
                 patch.object(ouc, '_spent_blocks', side_effect=spent), \
                 patch.object(ouc, 'blacklist_state', side_effect=blacklist), \
                 patch.object(ouc, 'host_direct_ip', return_value='198.51.100.9'), \
                 patch.object(ouc.Bridge, 'ensure'), \
                 patch.object(ouc, '_activate_backend'), \
                 patch.object(ouc, 'verify_request', return_value={'ok': True}):
                results = [ouc._rotate_psiphon(cfg, lambda *a: None, state, 'test') for _ in range(3)]
            self.assertTrue(all(r['ok'] for r in results), results)
            self.assertEqual(regions, ['US', 'DE', 'US', 'clean:US'])
            self.assertTrue(results[2]['attempts'][0]['cache_bust'])
            self.assertEqual(results[2]['new'], '192.0.2.3')
            self.assertEqual(state.load()['psiphon_region_index'], 1)


    def test_failed_clean_bootstrap_advances_to_next_region(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = SimpleNamespace(rotation_lock=str(Path(tmp) / 'rotation.lock'),
                psiphon_regions=['US', 'DE'], psiphon_start_wait=1, rotation_timeout=5,
                psiphon_socks_port=9060, proxy_http_port=8080, pin=False,
                proxy_mode=True, kill_switch=False)
            state = FakeState(active_backend='psiphon', fallback_activated_at=1,
                              psiphon_region_index=0, psiphon_cycle_visited=['US', 'DE'],
                              psiphon_cycle_count=1)
            calls = []
            class Tunnel:
                def __init__(self, *args): pass
                def available(self): return True, 'fake'
                def start(self, region, **kw):
                    calls.append(region)
                    return True, 'connected'
                def rebootstrap(self, region):
                    calls.append('clean:' + region)
                    return False, 'datastore restored: timeout'
                def pid(self): return 0
            probes = iter(['192.0.2.2', '192.0.2.1', '192.0.2.3'])
            with patch.object(ouc, 'Psiphon', Tunnel), \
                 patch.object(ouc, '_wait_rotation_spacing'), \
                 patch.object(ouc, 'probe_egress', side_effect=lambda *a, **k: next(probes)), \
                 patch.object(ouc, '_spent_blocks', return_value={'192.0.2.1/32'}), \
                 patch.object(ouc, 'blacklist_state', return_value=([], {})), \
                 patch.object(ouc, 'host_direct_ip', return_value='198.51.100.9'), \
                 patch.object(ouc.Bridge, 'ensure'), \
                 patch.object(ouc, '_activate_backend'), \
                 patch.object(ouc, 'verify_request', return_value={'ok': True}):
                result = ouc._rotate_psiphon(cfg, lambda *a: None, state, 'test')
            self.assertTrue(result['ok'], result)
            self.assertEqual(calls, ['US', 'clean:US', 'DE'])
            self.assertIn('restored', result['attempts'][0]['reason'])
            self.assertEqual(result['new'], '192.0.2.3')


if __name__ == '__main__':
    unittest.main(verbosity=2)
