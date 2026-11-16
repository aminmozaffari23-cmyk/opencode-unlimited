#!/usr/bin/env python3
"""Real local bridge connections: telemetry rotation, modes and redaction."""
import errno
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch
from pingpong_fixture import Fixture, ROOT
import test_return_lifecycle

ouc = test_return_lifecycle.ouc


class BridgeLogTest(unittest.TestCase):
    def test_real_bridge_rotates_private_redacted_jsonl(self):
        with Fixture() as f, socket.socket() as server:
            server.bind(('127.0.0.1', 0)); server.listen(); server.settimeout(.1)
            done = threading.Event()
            def echo():
                while not done.is_set():
                    try: conn, _ = server.accept()
                    except socket.timeout: continue
                    except OSError: return
                    def handle(c):
                        with c:
                            try:
                                while True:
                                    data = c.recv(4096)
                                    if not data: return
                                    c.sendall(data)
                            except OSError: pass
                    threading.Thread(target=handle, args=(conn,), daemon=True).start()
            thread = threading.Thread(target=echo, daemon=True); thread.start()
            self.addCleanup(done.set)
            f.env.update(OUC_BRIDGE_LOG_MAX_BYTES='2048', OUC_BRIDGE_LOG_BACKUPS='2', OUC_BRIDGE_LOG_REDACT='1')
            state = {'active_backend':'warp', 'active_socks_port': f.warp_port, 'hops':0}
            f.write_state(state)
            f.start([sys.executable, str(ROOT/'ouc.py'), 'bridge', '--listen', f'127.0.0.1:{f.bridge_port}', '--dynamic'])
            f.wait_port(f.bridge_port)
            target = server.getsockname()[1]
            for i in range(12):
                with socket.create_connection(('127.0.0.1', f.bridge_port), timeout=5) as c:
                    c.settimeout(5)
                    c.sendall(f'CONNECT localhost:{target} HTTP/1.1\r\nHost: localhost:{target}\r\n\r\n'.encode())
                    header = b''
                    while b'\r\n\r\n' not in header: header += c.recv(4096)
                    self.assertIn(b'200', header)
                    c.sendall(b'payload'); self.assertEqual(c.recv(7), b'payload')
                    state['hops'] = i + 1
                    f.write_state(state)
                    self.assertEqual(c.recv(1), b'')
            time.sleep(.3)
            path = f.state/'bridge-events.jsonl'
            files = [path, Path(str(path)+'.1'), Path(str(path)+'.2')]
            events = []
            for p in files:
                self.assertTrue(p.exists(), p)
                self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
                for line in p.read_text().splitlines():
                    self.assertNotIn('localhost', line)
                    events.append(json.loads(line))
            self.assertFalse(Path(str(path)+'.3').exists())
            self.assertEqual({e['event'] for e in events}, {'connect','epoch_drain','close'})
            expected = 'sha256:' + hashlib.sha256(b'localhost').hexdigest()
            self.assertTrue(all(e.get('target_host') == expected for e in events))
            self.assertTrue(any(e.get('bytes_up',0)>0 and e.get('bytes_down',0)>0 for e in events if e['event']=='close'))
            self.assertTrue(all(p.stat().st_size <= 2048 for p in files))

    def test_config_file_and_environment_overrides(self):
        with tempfile_dir() as td:
            Path(td, 'config.json').write_text(json.dumps({'bridge_log_max_bytes':1234,
                'bridge_log_backups':3, 'bridge_log_redact':True}))
            with patch.dict(os.environ, {'OUC_STATE_DIR':td}, clear=True):
                cfg = ouc.Config()
                self.assertEqual((cfg.bridge_log_max_bytes, cfg.bridge_log_backups, cfg.bridge_log_redact), (1234,3,True))
                self.assertEqual(cfg.as_dict()['bridge_log_max_bytes'], 1234)
            with patch.dict(os.environ, {'OUC_STATE_DIR':td, 'OUC_BRIDGE_LOG_MAX_BYTES':'0',
                                        'OUC_BRIDGE_LOG_BACKUPS':'1', 'OUC_BRIDGE_LOG_REDACT':'0'}, clear=True):
                cfg = ouc.Config()
                self.assertEqual((cfg.bridge_log_max_bytes, cfg.bridge_log_backups, cfg.bridge_log_redact), (0,1,False))

    def test_logging_errors_do_not_escape(self):
        with tempfile_dir() as td:
            logger = ouc.BridgeTelemetry(str(Path(td)/'events'), 10, 2, False)
            for err in (errno.ENOSPC, errno.EACCES):
                with patch.object(ouc.os, 'open', side_effect=OSError(err, 'injected')):
                    logger.emit('connect', target_host='localhost')


def tempfile_dir():
    import tempfile
    return tempfile.TemporaryDirectory()


if __name__ == '__main__':
    unittest.main(verbosity=2)
