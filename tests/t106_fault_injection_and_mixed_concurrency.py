#!/usr/bin/env python3
"""Offline datastore and state-write fault injection; no provider access."""
import errno
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from test_return_lifecycle import ouc, CacheTransaction


class FilesystemFaults(CacheTransaction):
    def test_backup_rename_errors_preserve_original(self):
        for code in (errno.ENOSPC, errno.EACCES, errno.EROFS):
            with self.subTest(errno=code), patch.object(ouc.os, 'rename', side_effect=OSError(code, 'injected backup')):
                ok, detail = self.ps.rebootstrap('US')
                self.assertFalse(ok)
                self.assertIn('injected backup', detail)
                self.assertEqual((self.root/'session.db').read_bytes(), b'old-session')
                self.assertEqual((self.root/'config.json').read_text(), '{"bootstrap":true}')

    def test_restore_error_keeps_old_backup_and_reports_error(self):
        real_rename = os.rename
        backup = Path(str(self.root)+'.ouc-cache-backup')
        def rename(src, dst):
            if str(src) == str(backup):
                raise OSError(errno.EACCES, 'injected restore')
            return real_rename(src, dst)
        with patch.object(ouc.os, 'rename', side_effect=rename), patch.object(self.ps, 'start', return_value=(False, 'timeout')):
            ok, detail = self.ps.rebootstrap('US')
        self.assertFalse(ok)
        self.assertIn('injected restore', detail)
        self.assertEqual((backup/'session.db').read_bytes(), b'old-session')
        self.assertEqual((backup/'config.json').read_text(), '{"bootstrap":true}')

    def test_killed_bootstrap_child_restores_cache(self):
        def killed(region):
            proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
            try:
                os.kill(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)
                return False, 'child killed during bootstrap'
            finally:
                if proc.poll() is None: proc.kill(); proc.wait()
        with patch.object(self.ps, 'start', side_effect=killed):
            ok, detail = self.ps.rebootstrap('DE')
        self.assertFalse(ok)
        self.assertIn('restored', detail)
        self.assertEqual((self.root/'session.db').read_bytes(), b'old-session')
        self.assertEqual((self.root/'config.json').read_text(), '{"bootstrap":true}')

    def test_atomic_state_replace_failure_keeps_original_json(self):
        path = self.root/'state.json'
        original = {'active_backend':'psiphon', 'active_socks_port':9060}
        path.write_text(json.dumps(original))
        for code in (errno.ENOSPC, errno.EACCES):
            with self.subTest(errno=code), patch.object(ouc.os, 'replace', side_effect=OSError(code, 'injected state rename')):
                with self.assertRaises(OSError):
                    ouc.atomic_write_json(str(path), {'active_backend':'warp'})
                self.assertEqual(json.loads(path.read_text()), original)
                self.assertEqual(list(self.root.glob('.tmp-*.json')), [])

    def test_restore_failure_aborts_region_search_and_persists_error(self):
        from types import SimpleNamespace
        from test_return_lifecycle import FakeState
        backup = Path(str(self.root) + '.ouc-cache-backup')
        backup.mkdir()
        cfg = SimpleNamespace(rotation_lock=str(self.root/'rotation.lock'), psiphon_data=str(self.root),
            psiphon_regions=['US','DE'], psiphon_start_wait=1, rotation_timeout=5,
            psiphon_socks_port=9060, proxy_socks_port=40000, proxy_http_port=8080,
            pin=False, proxy_mode=True, kill_switch=False)
        state = FakeState(active_backend='psiphon', fallback_activated_at=1,
            psiphon_region_index=0, psiphon_cycle_count=1, psiphon_cycle_visited=['US','DE'])
        calls = []
        class Tunnel:
            def __init__(self, *args): pass
            def available(self): return True, 'fake'
            def start(self, region, **kw): calls.append(region); return True, 'connected'
            def rebootstrap(self, region): return False, 'cache recovery failed: injected EACCES'
            def pid(self): return 0
        probes = iter(['192.0.2.2', '192.0.2.1'])
        with patch.object(ouc, 'Psiphon', Tunnel), patch.object(ouc, '_wait_rotation_spacing'), \
             patch.object(ouc, 'probe_egress', side_effect=lambda *a, **k: next(probes)), \
             patch.object(ouc, '_spent_blocks', return_value={'192.0.2.1/32'}), \
             patch.object(ouc, 'host_direct_ip', return_value='198.51.100.9'), \
             patch.object(ouc.Bridge, 'ensure'):
            result = ouc._rotate_psiphon(cfg, lambda *a: None, state, 'test')
        self.assertFalse(result['ok'])
        self.assertEqual(state.load()['last_error'], 'cache recovery failed: injected EACCES')
        self.assertEqual(calls, ['US'])
        with ouc.StateLock(cfg.rotation_lock, blocking=False, fix_mode=False):
            pass


class MixedConcurrency(unittest.TestCase):
    def test_mixed_success_and_contention(self):
        import concurrent.futures
        from pingpong_fixture import Fixture
        with Fixture() as f:
            initial = f.command('hop', '--backend', 'psiphon')
            self.assertEqual(initial.returncode, 0, initial.stdout + initial.stderr)
            f.env['FAKE_WARP_KEYS_DELAY_MS'] = '800'
            import threading, time
            stop = threading.Event()
            observations, bad_state = [], []
            config = str(f.state/'psiphon'/'psiphon.config').encode()
            listen = ('127.0.0.1:' + str(f.bridge_port)).encode()
            def monitor():
                while not stop.wait(.05):
                    try: json.loads((f.state/'state.json').read_text())
                    except (OSError, ValueError) as exc: bad_state.append(str(exc))
                    tunnels = bridges = 0
                    for proc in Path('/proc').iterdir():
                        if not proc.name.isdigit(): continue
                        try: cmd = (proc/'cmdline').read_bytes()
                        except OSError: continue
                        tunnels += int(config in cmd and b'-config' in cmd)
                        bridges += int(b'ouc.py' in cmd and b'bridge' in cmd and listen in cmd)
                    observations.append((tunnels, bridges))
            watcher = threading.Thread(target=monitor, daemon=True)
            watcher.start()
            commands = [('hop','--backend','warp'), ('hop','--backend','psiphon'),
                        ('repair',), ('status','--json'), ('doctor',),
                        ('hop','--backend','warp'), ('status','--json')]
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(commands)) as pool:
                    results = list(pool.map(lambda args: f.command(*args, timeout=60), commands))
            finally:
                stop.set(); watcher.join(5)
            self.assertTrue(observations)
            self.assertFalse(bad_state, bad_state)
            self.assertTrue(all(t <= 1 and b <= 1 for t,b in observations), observations)
            for args, result in zip(commands, results):
                self.assertNotIn('Traceback (most recent call last)', result.stdout + result.stderr)
                if args[0] == 'status':
                    data = json.loads(result.stdout)
                    self.assertIsInstance(data, dict)
            refused = [r for a,r in zip(commands,results) if a[0] in ('hop','repair')
                       and 'rotation already in progress' in r.stdout+r.stderr]
            self.assertTrue(refused, [(a,r.returncode,r.stdout) for a,r in zip(commands,results)])
            self.assertTrue(any(r.returncode == 0 for a,r in zip(commands,results) if a[0] in ('hop','repair')))
            json.loads((f.state/'state.json').read_text())
            # The controller's actual config path identifies its tunnel.
            config = str(f.state/'psiphon'/'psiphon.config').encode()
            owned = []
            for proc in Path('/proc').iterdir():
                if not proc.name.isdigit(): continue
                try: cmd = (proc/'cmdline').read_bytes()
                except OSError: continue
                if config in cmd and b'-config' in cmd: owned.append(proc.name)
            self.assertLessEqual(len(owned), 1, owned)


if __name__ == '__main__':
    unittest.main(verbosity=2)
