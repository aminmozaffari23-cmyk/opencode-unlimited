"""Focused offline return-policy tests; all controller side effects are mocked.

These tests do not establish live routing, TUI behavior, or datastore safety.
"""
import importlib.util
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('ouc_return_test', Path(__file__).resolve().parents[1] / 'ouc.py')
ouc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ouc)


class FakeState:
    def __init__(self, **fields):
        self.data = dict(fields)
    def load(self):
        return dict(self.data)
    def update(self, **fields):
        self.data.update(fields)
        return self.load()


class ReturnPolicy(TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.cfg = SimpleNamespace(rotation_lock=str(Path(temp.name) / 'rotation.lock'),
                                   backend_mode='auto', psiphon_regions=['US', 'DE'],
                                   blacklist_ttl=14400, proxy_socks_port=40000,
                                   psiphon_socks_port=9060, proxy_mode=True,
                                   warp_bin='/fake/warp', warp_fallback_threshold=5)
        self.state = FakeState(active_backend='psiphon', active_socks_port=9060,
                               fallback_activated_at=100, psiphon_region_index=1)
        self.log = lambda *args: None

    def test_expired_ttl_selects_warp_but_unexpired_does_not(self):
        with patch.object(ouc, 'now', return_value=100 + 14400), patch.object(ouc.Psiphon, 'available', return_value=(True, 'fake')):
            self.assertEqual(ouc.choose_backend(self.cfg, self.state), 'warp')
        with patch.object(ouc, 'now', return_value=100 + 14399), patch.object(ouc.Psiphon, 'available', return_value=(True, 'fake')):
            self.assertEqual(ouc.choose_backend(self.cfg, self.state), 'psiphon')

    def test_full_cycle_selects_warp_before_ttl(self):
        self.state.update(psiphon_region_index=0, psiphon_cycle_visited=['US', 'DE'])
        with patch.object(ouc, 'now', return_value=101), patch.object(ouc.Psiphon, 'available', return_value=(True, 'fake')):
            self.assertEqual(ouc.choose_backend(self.cfg, self.state), 'warp')
        self.state.update(psiphon_cycle_visited=['US'])
        with patch.object(ouc, 'now', return_value=101), patch.object(ouc.Psiphon, 'available', return_value=(True, 'fake')):
            self.assertEqual(ouc.choose_backend(self.cfg, self.state), 'psiphon')

    def test_failed_automatic_return_immediately_bounces(self):
        failed = {'ok': False, 'error': 'WARP walled', 'attempts': [{}]}
        recovered = {'ok': True, 'backend': 'psiphon'}
        def psiphon_recovery(*args):
            self.state.update(active_backend='psiphon', active_socks_port=9060)
            return recovered
        with patch.object(ouc, 'choose_backend', return_value='warp'), patch.object(ouc, 'Pinner'), \
             patch.object(ouc, '_restore_warp_tun'), patch.object(ouc, '_clear_warp_tun_for_psiphon'), \
             patch.object(ouc.Bridge, 'ensure'), patch.object(ouc, '_rotate_warp', return_value=failed), \
             patch.object(ouc, '_rotate_psiphon', side_effect=psiphon_recovery) as bounce, \
             patch.object(ouc, 'now', return_value=15000):
            result = ouc.rotate(self.cfg, self.log, self.state)
        self.assertTrue(result['ok'])
        self.assertTrue(result['returned_after_failed_warp'])
        self.assertEqual(self.state.data['active_socks_port'], 9060)
        self.assertEqual(self.state.data['fallback_activated_at'], 15000)
        bounce.assert_called_once()

    def test_successful_return_stops_psiphon(self):
        with patch.object(ouc, 'choose_backend', return_value='warp'), patch.object(ouc, 'Pinner'), \
             patch.object(ouc, '_restore_warp_tun'), patch.object(ouc.Bridge, 'ensure'), \
             patch.object(ouc, '_rotate_warp', return_value={'ok': True}), \
             patch.object(ouc, '_activate_backend') as activate, patch.object(ouc.Psiphon, 'stop') as stop:
            result = ouc.rotate(self.cfg, self.log, self.state)
        self.assertTrue(result['ok'])
        activate.assert_called_once()
        stop.assert_called_once()

    def test_explicit_warp_failure_not_misreported_as_psiphon_success(self):
        failed = {'ok': False, 'error': 'WARP walled', 'attempts': [{}]}
        with patch.object(ouc, 'choose_backend', return_value='warp'), patch.object(ouc, 'Pinner'), \
             patch.object(ouc, '_restore_warp_tun'), patch.object(ouc.Bridge, 'ensure'), \
             patch.object(ouc, '_rotate_warp', return_value=failed), \
             patch.object(ouc, '_rotate_psiphon') as bounce:
            result = ouc.rotate(self.cfg, self.log, self.state, backend='warp')
        self.assertFalse(result['ok'])
        bounce.assert_not_called()

    def test_warp_mode_failure_does_not_escalate(self):
        self.cfg.backend_mode = 'warp'
        self.cfg.warp_fallback_threshold = 1
        with patch.object(ouc, 'choose_backend', return_value='warp'), patch.object(ouc, 'Pinner'), \
             patch.object(ouc, '_restore_warp_tun'), patch.object(ouc.Bridge, 'ensure'), \
             patch.object(ouc, '_rotate_warp', return_value={'ok': False, 'attempts': [{}]}), \
             patch.object(ouc, '_rotate_psiphon') as bounce:
            result = ouc.rotate(self.cfg, self.log, self.state)
        self.assertFalse(result['ok'])
        bounce.assert_not_called()

    def test_same_process_second_thread_is_refused_without_state_mutation(self):
        ready = threading.Event()
        release = threading.Event()
        def holder():
            with ouc.StateLock(self.cfg.rotation_lock, blocking=False, fix_mode=False):
                ready.set()
                release.wait(10)
        thread = threading.Thread(target=holder)
        thread.start()
        self.addCleanup(lambda: (release.set(), thread.join(10)))
        self.assertTrue(ready.wait(5))
        before = self.state.load()
        with patch.object(ouc, '_rotate_locked') as implementation:
            result = ouc.rotate(self.cfg, self.log, self.state, backend='warp')
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'rotation already in progress')
        self.assertEqual(self.state.load(), before)
        implementation.assert_not_called()

    def test_status_refresh_does_not_write_while_another_thread_rotates(self):
        ready = threading.Event()
        release = threading.Event()
        def holder():
            with ouc.StateLock(self.cfg.rotation_lock, blocking=False, fix_mode=False):
                ready.set()
                release.wait(10)
        thread = threading.Thread(target=holder)
        thread.start()
        self.addCleanup(lambda: (release.set(), thread.join(10)))
        self.assertTrue(ready.wait(5))
        state = FakeState(current='2001:db8::1')
        state.save = lambda data: self.fail('status wrote during rotation')
        ouc.sync_current(self.cfg, state, egress='2001:db8::2')
        self.assertEqual(state.load()['current'], '2001:db8::1')

    def test_same_thread_reenters_and_other_thread_blocking_waits(self):
        with ouc.StateLock(self.cfg.rotation_lock, blocking=False, fix_mode=False):
            with ouc.StateLock(self.cfg.rotation_lock, blocking=False, fix_mode=False):
                pass
            obtained = threading.Event()
            def waiter():
                with ouc.StateLock(self.cfg.rotation_lock, blocking=True, fix_mode=False):
                    obtained.set()
            thread = threading.Thread(target=waiter)
            thread.start()
            self.assertFalse(obtained.wait(0.1))
        thread.join(5)
        self.assertTrue(obtained.is_set())

class CacheTransaction(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'data'
        self.root.mkdir()
        (self.root / 'config.json').write_text('{"bootstrap":true}')
        (self.root / 'session.db').write_bytes(b'old-session')
        self.cfg = SimpleNamespace(psiphon_data=str(self.root),
                                   psiphon_config=str(self.root / 'config.json'))
        self.ps = ouc.Psiphon(self.cfg)
        self.stop = patch.object(self.ps, 'stop').start()
        self.addCleanup(patch.stopall)
        patch.object(self.ps, '_orphan_pids', return_value=[]).start()
        patch.object(self.ps, 'socks_open', return_value=False).start()

    def test_failed_bootstrap_restores_original_bytes(self):
        def start(region):
            self.assertTrue((self.root / 'config.json').exists())
            self.assertFalse((self.root / 'session.db').exists())
            (self.root / 'new.db').write_bytes(b'bad')
            return False, 'timeout'
        with patch.object(self.ps, 'start', side_effect=start):
            ok, detail = self.ps.rebootstrap('US')
        self.assertFalse(ok)
        self.assertIn('restored', detail)
        self.assertEqual((self.root / 'session.db').read_bytes(), b'old-session')
        self.assertFalse((self.root / 'new.db').exists())
        self.assertEqual((self.root / 'config.json').read_text(), '{"bootstrap":true}')
        self.assertFalse(Path(str(self.root) + '.ouc-cache-backup').exists())

    def test_success_discards_old_cache_only_after_tunnel_connects(self):
        def start(region):
            self.assertTrue(Path(str(self.root) + '.ouc-cache-backup/session.db').exists())
            self.assertFalse((self.root / 'session.db').exists())
            (self.root / 'new.db').write_bytes(b'new-session')
            return True, 'connected'
        with patch.object(self.ps, 'start', side_effect=start):
            ok, _ = self.ps.rebootstrap('DE')
        self.assertTrue(ok)
        self.assertEqual((self.root / 'new.db').read_bytes(), b'new-session')
        self.assertFalse(Path(str(self.root) + '.ouc-cache-backup').exists())

    def test_interrupted_backup_is_restored_before_next_attempt(self):
        backup = Path(str(self.root) + '.ouc-cache-backup')
        self.root.rename(backup)
        self.root.mkdir()
        (self.root / 'partial.db').write_text('partial')
        with patch.object(self.ps, 'start', return_value=(False, 'timeout')):
            ok, detail = self.ps.rebootstrap('US')
        self.assertFalse(ok)
        self.assertIn('restored', detail)
        self.assertEqual((self.root / 'session.db').read_bytes(), b'old-session')
        self.assertFalse((self.root / 'partial.db').exists())
        self.assertFalse(backup.exists())
