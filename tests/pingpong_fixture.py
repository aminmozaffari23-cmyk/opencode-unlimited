"""Local-only stand-ins for ping-pong integration tests; never uses a provider."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'tests' / 'helpers'


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class Fixture:
    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='ouc-test.')
        self.root = Path(self.tmp.name)
        self.state = self.root / 'state'
        self.state.mkdir()
        (self.root / 'home').mkdir()
        self.warp_port, self.ps_port, self.bridge_port = free_port(), free_port(), free_port()
        self.env = dict(os.environ)
        self.env.update(OUC_TEST_SANDBOX=str(self.root), OUC_STATE_DIR=str(self.state),
            OUC_TARGET_HOME=str(self.root / 'home'), OUC_USER_STATE_DIR=str(self.root / 'home' / 'user-state'),
            OUC_HOSTS_FILE=str(self.root / 'hosts'), OUC_INSTANCE_LOCK=str(self.state / 'supervisor.lock'),
            OUC_WARP_BIN=str(HELPER / 'fake_warp.py'), OUC_PSIPHON_BIN=str(HELPER / 'fake_psiphon.py'),
            OUC_IPTABLES=str(HELPER / 'fake_iptables.py'), OUC_IP6TABLES=str(HELPER / 'fake_iptables.py'),
            OUC_PROBE_CMD=str(HELPER / 'fake_probe_backends.sh'), OUC_OC_BIN=str(HELPER / 'fake_opencode.sh'),
            FAKE_WARP_STATE=str(self.root / 'warp.json'), FAKE_WARP_LOG=str(self.root / 'warp.log'),
            FAKE_PSIPHON_STATE=str(self.root / 'ps.json'), FAKE_PSIPHON_LOG=str(self.root / 'ps.log'),
            FAKE_IPT_STATE=str(self.root / 'ipt.json'), FAKE_IPT_LOG=str(self.root / 'ipt.log'),
            FAKE_PSIPHON_FORWARD='1', FAKE_WARP_MIN_SETTLE='0', OUC_WARP_MODE='proxy',
            OUC_PROXY_SOCKS_PORT=str(self.warp_port), OUC_PROXY_HTTP_PORT=str(self.bridge_port),
            OUC_PSIPHON_SOCKS_PORT=str(self.ps_port), OUC_PSIPHON_REGIONS='US,DE',
            OUC_PSIPHON_START_WAIT='5', OUC_HOST_DIRECT_IP='198.51.100.9', OUC_PIN='0',
            OUC_DISABLE_KILL_SWITCH='1', OUC_VERIFY='0', OUC_ROTATION_MODE='keys',
            OUC_MIN_ROTATE_GAP='0', OUC_ROTATE_GAP_JITTER='0', OUC_COOLDOWN_SECONDS='0',
            OUC_CONNECT_WAIT='3', OUC_EGRESS_WAIT='3', OUC_KEYS_ATTEMPTS='1', OUC_IGNORE_TUN_CHECK='1',
            OUC_WATCHDOG='0')
        (self.root / 'warp.json').write_text(json.dumps({'connected': True, 'registered': True, 'reg_count': 1, 'deleted_at': 0}))
        (self.root / 'ps.json').write_text(json.dumps({'ips':['192.0.2.10','192.0.2.11','192.0.2.12','192.0.2.13','192.0.2.14'], 'index':0}))
        (self.root / 'hosts').write_text('127.0.0.1 localhost\n')
        self.procs = []
        self.start([sys.executable, str(HELPER/'fake_socks5.py'), '--port', str(self.warp_port)])
        self.wait_port(self.warp_port)
        return self

    def start(self, cmd):
        p = subprocess.Popen(cmd, env=self.env, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.procs.append(p)
        return p

    def command(self, *args, extra=None, timeout=90):
        env = dict(self.env, **(extra or {}))
        prefix = [] if os.geteuid() == 0 else ['sudo', '-n', 'env'] + [f'{k}={v}' for k,v in env.items() if k.startswith(('OUC_', 'FAKE_'))]
        return subprocess.run(prefix + [sys.executable, str(ROOT/'ouc.py'), *args], cwd=ROOT,
                              env=env, capture_output=True, text=True, timeout=timeout)

    def wait_port(self, port):
        for _ in range(100):
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=.1): return
            except OSError: time.sleep(.05)
        raise RuntimeError(f'port {port} not listening')

    def read_state(self):
        return json.loads((self.state/'state.json').read_text())

    def write_state(self, value):
        path = self.state/'state.json'
        if os.geteuid() == 0:
            path.write_text(json.dumps(value))
        else:
            subprocess.run(['sudo', '-n', 'tee', str(path)], input=json.dumps(value),
                           text=True, stdout=subprocess.DEVNULL, check=True)

    def __exit__(self, *args):
        self.command('stop', timeout=30)
        for p in self.procs:
            p.terminate()
            try: p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill(); p.wait()
        if os.geteuid() != 0:
            subprocess.run(['sudo', '-n', 'chown', '-R', f'{os.getuid()}:{os.getgid()}', str(self.root)], capture_output=True)
        self.tmp.cleanup()
