#!/usr/bin/env python3
"""Offline persistent PTY stand-in, real CLI hops and bridge telemetry."""
import json
import os
import pty
import select
import socket
import subprocess
import sys
import threading
import time
import unittest
from pingpong_fixture import Fixture

STANDIN = r'''
import socket,sys
bridge=('127.0.0.1',int(sys.argv[1])); target=int(sys.argv[2]); s=None
print('READY',flush=True)
for line in sys.stdin:
    msg=line.strip()
    if not msg: continue
    try:
        if s is None: raise OSError('connect')
        s.sendall((msg+'\n').encode()); data=s.recv(4096)
        if msg.encode() not in data: raise OSError('drained')
    except OSError:
        if s: s.close()
        s=socket.create_connection(bridge,timeout=5)
        s.sendall(f'CONNECT 127.0.0.1:{target} HTTP/1.1\r\nHost: 127.0.0.1:{target}\r\n\r\n'.encode())
        if b'200' not in s.recv(4096): raise RuntimeError('CONNECT failed')
        s.sendall((msg+'\n').encode()); data=s.recv(4096)
        if msg.encode() not in data: raise RuntimeError('echo failed')
    print('RENDERED:'+msg,flush=True)
'''


class PingPong(unittest.TestCase):
    def test_four_turns_and_three_real_cli_hops(self):
        with Fixture() as f, socket.socket() as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(('127.0.0.1', 0)); server.listen()
            server.settimeout(.2)
            stopped = threading.Event()
            def serve():
                while not stopped.is_set():
                    try: conn, _ = server.accept()
                    except socket.timeout: continue
                    except OSError: break
                    def echo(c):
                        with c:
                            try:
                                while True:
                                    data=c.recv(4096)
                                    if not data: break
                                    c.sendall(data)
                            except OSError: pass
                    threading.Thread(target=echo, args=(conn,), daemon=True).start()
            threading.Thread(target=serve, daemon=True).start()
            self.addCleanup(stopped.set)
            r = f.command('hop', '--backend', 'warp')
            self.assertEqual(r.returncode, 0, r.stdout+r.stderr)
            f.wait_port(f.bridge_port)
            master, slave = pty.openpty()
            child = subprocess.Popen([sys.executable, '-u', '-c', STANDIN, str(f.bridge_port), str(server.getsockname()[1])], stdin=slave, stdout=slave, stderr=slave)
            os.close(slave)
            self.addCleanup(os.close, master)
            self.addCleanup(lambda: child.kill() if child.poll() is None else None)
            data = b''
            def until(token):
                nonlocal data
                end = time.monotonic()+15
                while token.encode() not in data and time.monotonic() < end:
                    if select.select([master], [], [], .2)[0]:
                        data += os.read(master, 4096)
                self.assertIn(token.encode(), data, data.decode(errors='replace'))
            until('READY')
            pid = child.pid
            for i, backend in enumerate(('warp','psiphon','warp','psiphon'), 1):
                if i > 1:
                    hop = f.command('hop', '--backend', backend)
                    self.assertEqual(hop.returncode, 0, hop.stdout+hop.stderr)
                    self.assertEqual(f.read_state()['active_backend'], backend)
                    if backend == 'warp':
                        self.assertFalse((f.state/'psiphon'/'psiphon.pid').exists())
                    time.sleep(1)
                os.write(master, f'turn{i}\n'.encode())
                until(f'RENDERED:turn{i}')
                self.assertEqual(child.pid, pid)
                self.assertIsNone(child.poll())
            child.terminate(); child.wait(timeout=5); time.sleep(1)
            events = [json.loads(line) for line in (f.state/'bridge-events.jsonl').read_text().splitlines()]
            connects = [e for e in events if e['event'] == 'connect']
            drains = [e for e in events if e['event'] == 'epoch_drain']
            closes = [e for e in events if e['event'] == 'close']
            self.assertGreaterEqual(len(connects), 4)
            self.assertGreaterEqual(len(drains), 3)
            self.assertGreaterEqual(sum(bool(c.get('drained') and c.get('bytes_up',0) and c.get('bytes_down',0)) for c in closes), 3)

if __name__ == '__main__':
    unittest.main(verbosity=2)
