#!/usr/bin/env python3
"""
opencode-unlimited (corrected) — single-file controller.

What this does
--------------
Keeps a Cloudflare WARP tunnel up, and when opencode's free tier reports that
the daily quota is exhausted, re-registers WARP so you land on a fresh IPv6
/64 (a fresh quota bucket).

Hard rules this implementation follows
--------------------------------------
* It never modifies, pipes or signs opencode's output. No advertising.
* It never leaves the machine without internet: if the tunnel cannot be
  repaired, the leak-guard firewall is left *open* (degraded mode) and the
  state is reported loudly, instead of blackholing traffic.
* Privileged work happens in a root supervisor, so no sudoers entry is needed.

Commands
--------
  ouc install          install unit / supervisor (called by install.sh)
  ouc uninstall        remove everything this tool created (incl. legacy)
  ouc start|stop|restart|status
  ouc supervise        run the supervisor loop (used by systemd / portable daemon)
  ouc hop              rotate to a fresh /64 now
  ouc repair           force tunnel repair, then re-arm the firewall
  ouc rescue           stop everything and make sure internet works again
  ouc killswitch on|off|status
  ouc doctor           health check
  ouc lane [args...]   launch opencode with opencode.ai pinned to IPv6 /64
  ouc scan-once        run one quota-detection pass (debug / tests)
  ouc config           print the effective configuration
  ouc version

Everything is Python 3 standard library. No network calls except:
  * cloudflare WARP (via warp-cli)
  * the IPv6 egress probe (api64.ipify.org by default)
  * a DNS AAAA lookup for opencode.ai in `ouc lane`
"""

from __future__ import annotations

import argparse
import base64
import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import traceback
import tempfile
import threading
import time
import urllib.request
import uuid

VERSION = "2.3.0"
BRAND = "opencode-unlimited"
DEFAULT_STATE_DIR = "/var/lib/opencode-unlimited"
UNIT_NAME = "opencode-unlimited.service"
UNIT_DIR = os.environ.get("OUC_UNIT_DIR", "/etc/systemd/system")
UNIT_PATH = os.path.join(UNIT_DIR, UNIT_NAME)
LIB_DIR = os.environ.get("OUC_LIB_DIR", "/usr/local/lib/opencode-unlimited")
BIN_PATH = os.environ.get("OUC_BIN_PATH", "/usr/local/bin/ouc")

# Quota-wall signatures. opencode >= 1.19 writes:
#   AI_APICallError: Free usage limit reached. Free models work in OpenCode ...
# Older builds wrote a QuotaExceeded error; keep it for compatibility.
WALL_PATTERNS = (b"Free usage limit reached", b"QuotaExceeded")

# What we say after the wall is gone. It must ask for continuation, not for a
# restart: "continue" keeps the context, "try again" makes the model redo work.
DEFAULT_RESUME_PROMPT = (
    "Your previous answer was cut off by the provider's free-usage limit. "
    "Continue exactly from where you stopped: do not repeat finished work and "
    "do not re-read files you have already read. If a tool call was "
    "interrupted, redo only that call."
)
SESSION_RE = re.compile(r"session\.id=(ses_[A-Za-z0-9]+)")
MODEL_RE = re.compile(r"providerID=([\w.\-]+)\s+modelID=([\w.\-]+)")


# Marker used only to recognise the *old* adware file so we can delete it.
# Stored base64 so this repository contains no advertising text at all and
# static greps for the ad string come back clean.
LEGACY_AD_MARKER_B64 = "RWxlY3Ryb25DdXJzZWQ="  # see uninstall --legacy

MAX_LOG_READ = 8 * 1024 * 1024

# Hosts whose traffic must leave over IPv6 for the /64 rotation to matter.
PIN_HOSTS_DEFAULT = "opencode.ai,api.opencode.ai"
PIN_BEGIN = "# BEGIN opencode-unlimited (managed) — do not edit"
PIN_END = "# END opencode-unlimited"


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def which(*candidates: str) -> str | None:
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    for c in candidates:
        f = shutil.which(c) if c else None
        if f:
            return f
    return None


def now() -> int:
    return int(time.time())


def mkdirp(p: str, mode: int = 0o755) -> None:
    os.makedirs(p, mode=mode, exist_ok=True)


def atomic_write_json(path: str, data: dict, mode: int = 0o644) -> None:
    """Write JSON atomically (tmp + rename) so a crash can never truncate state."""
    d = os.path.dirname(path) or "."
    mkdirp(d)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def read_json(path: str, default=None):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return default


# --------------------------------------------------------------------------
# rotating log
# --------------------------------------------------------------------------

class Log:
    def __init__(self, path: str, max_bytes: int = 512 * 1024, backups: int = 3,
                 echo: bool = False):
        self.path = path
        self.max_bytes = max_bytes
        self.backups = backups
        self.echo = echo
        mkdirp(os.path.dirname(path) or ".")

    def _rotate(self) -> None:
        try:
            if not os.path.exists(self.path) or os.path.getsize(self.path) < self.max_bytes:
                return
            for i in range(self.backups - 1, 0, -1):
                src, dst = f"{self.path}.{i}", f"{self.path}.{i + 1}"
                if os.path.exists(src):
                    os.replace(src, dst)
            os.replace(self.path, f"{self.path}.1")
        except OSError:
            pass

    def __call__(self, msg: str, level: str = "INFO") -> None:
        line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {level:<5} {msg}"
        if self.echo:
            print(f"[ouc] {msg}", file=sys.stderr, flush=True)
        try:
            self._rotate()
            with open(self.path, "a") as fh:
                fh.write(line + "\n")
        except OSError:
            pass


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------

class Config:
    """Config = defaults <- config.json <- environment. Env always wins."""

    def __init__(self):
        self.state_dir = os.environ.get("OUC_STATE_DIR") or DEFAULT_STATE_DIR
        self.config_file = os.path.join(self.state_dir, "config.json")
        disk: dict = {}
        try:
            with open(self.config_file) as fh:
                disk = json.load(fh) or {}
        except Exception:
            disk = {}

        is_root = os.geteuid() == 0
        self.target_home = (
            os.environ.get("OUC_TARGET_HOME")
            or (disk.get("target_home") if is_root else None)
            or os.path.expanduser("~")
        )

        mode = os.environ.get("OUC_FORCE_MODE") or disk.get("mode")
        if not mode:
            # no recorded opinion: trust reality (an installed unit wins)
            mode = "systemd" if os.path.exists(UNIT_PATH) else "portable"
        self.mode = mode if mode in ("systemd", "portable") else "portable"

        # timings (seconds)
        self.settle = max(8, env_int("OUC_SETTLE_SECONDS", int(disk.get("settle", 9))))
        self.cooldown = env_int("OUC_COOLDOWN_SECONDS", int(disk.get("cooldown", 90)))
        self.tick = env_int("OUC_TICK_SECONDS", int(disk.get("tick", 2)))
        self.egress_wait = env_int("OUC_EGRESS_WAIT", int(disk.get("egress_wait", 20)))
        self.connect_wait = env_int("OUC_CONNECT_WAIT", int(disk.get("connect_wait", 20)))
        self.reg_retries = env_int("OUC_REG_RETRIES", int(disk.get("reg_retries", 3)))
        self.reg_backoff = max(2, env_int("OUC_REG_BACKOFF", int(disk.get("reg_backoff", 3))))
        self.rotation_timeout = env_int("OUC_ROTATION_TIMEOUT",
                                        int(disk.get("rotation_timeout", 45)))

        # kill-switch: OUC_DISABLE_KILL_SWITCH=1 always wins
        ks_default = bool(disk.get("kill_switch", True))
        self.kill_switch = ks_default and not env_bool("OUC_DISABLE_KILL_SWITCH", False)
        if env_bool("OUC_DISABLE_KILL_SWITCH", False):
            self.kill_switch_reason = "disabled by OUC_DISABLE_KILL_SWITCH=1"
        elif not ks_default:
            self.kill_switch_reason = "disabled at install time"
        else:
            self.kill_switch_reason = "enabled"

        # binaries / seams
        self.warp_bin = os.environ.get("OUC_WARP_BIN") or which(
            "warp-cli", "/usr/bin/warp-cli", "/usr/local/bin/warp-cli")
        self.iptables = os.environ.get("OUC_IPTABLES") or which(
            "iptables", "/usr/sbin/iptables", "/sbin/iptables")
        self.ip6tables = os.environ.get("OUC_IP6TABLES") or which(
            "ip6tables", "/usr/sbin/ip6tables", "/sbin/ip6tables")
        self.oc_bin = os.environ.get("OUC_OC_BIN") or os.path.join(
            self.target_home, ".opencode/bin/opencode")
        if not (os.path.isfile(self.oc_bin) and os.access(self.oc_bin, os.X_OK)):
            self.oc_bin = which("opencode", "/usr/local/bin/opencode", "/usr/bin/opencode") \
                or self.oc_bin
        # IPv6 pinning: make opencode reach its backend over the tunnel's /64
        # (measured: it prefers IPv4, and the quota is keyed on the source IP)
        self.pin = env_bool("OUC_PIN", bool(disk.get("pin", True)))
        self.pin_hosts = [h.strip() for h in os.environ.get(
            "OUC_PIN_HOSTS", os.environ.get("OUC_PIN_HOST", PIN_HOSTS_DEFAULT)).split(",") if h.strip()]
        self.hosts_file = os.environ.get("OUC_HOSTS_FILE", "/etc/hosts")
        self.pin_refresh = env_int("OUC_PIN_REFRESH", int(disk.get("pin_refresh", 6 * 3600)))
        # verification: prove a rotation with a real request, not with "the IP changed"
        self.verify = env_bool("OUC_VERIFY", bool(disk.get("verify", True)))
        self.verify_timeout = env_int("OUC_VERIFY_TIMEOUT", int(disk.get("verify_timeout", 25)))
        self.test_model = os.environ.get("OUC_TEST_MODEL", disk.get("test_model",
                                                                  "opencode/flint-40b-free"))
        self.keys_attempts = max(1, env_int("OUC_KEYS_ATTEMPTS",
                                            int(disk.get("keys_attempts", 4))))
        self.rotation_mode = (os.environ.get("OUC_ROTATION_MODE")
                              or disk.get("rotation_mode") or "auto").lower()
        self.blacklist_ttl = env_int("OUC_BLACKLIST_TTL", int(disk.get("blacklist_ttl", 4 * 3600)))
        # ---- auto-resume: after a quota wall, wait for a working block and
        #      continue the interrupted session instead of starting over ----
        self.auto_resume = env_bool("OUC_AUTO_RESUME",
                                    bool(disk.get("auto_resume", False)))
        self.resume_prompt = (os.environ.get("OUC_RESUME_PROMPT")
                              or disk.get("resume_prompt") or DEFAULT_RESUME_PROMPT)
        self.resume_max = max(1, env_int("OUC_RESUME_MAX",
                                         int(disk.get("resume_max", 3))))
        self.resume_backoff = max(0, env_int("OUC_RESUME_BACKOFF",
                                             int(disk.get("resume_backoff", 5))))
        self.resume_budget = env_int("OUC_RESUME_BUDGET",
                                     int(disk.get("resume_budget", 900)))
        self.resume_model = (os.environ.get("OUC_RESUME_MODEL")
                             or disk.get("resume_model") or "")
        # session ids are convenient but fragile: the id in the log may belong
        # to another directory or to the title agent, and then opencode answers
        # "Session not found". Default to -c (continue the last session here)
        # and keep -s as an opt-in that falls back automatically.
        self.resume_use_session = env_bool(
            "OUC_RESUME_USE_SESSION_ID", bool(disk.get("resume_use_session", False)))
        self.resume_wait_reset = env_bool(
            "OUC_RESUME_WAIT_RESET", bool(disk.get("resume_wait_reset", False)))
        self.resume_max_wait = env_int("OUC_RESUME_MAX_WAIT",
                                       int(disk.get("resume_max_wait", 0)))

        # ---- WARP mode: "warp" (TUN + kill-switch) or "proxy" (no TUN) ----
        # Containers (Codespaces, Docker, CI, unprivileged pods) have no TUN
        # device and no CAP_NET_ADMIN. WARP's proxy mode needs neither: it
        # publishes a local SOCKS5 proxy and still reaches the IPv6 internet,
        # so the /64 rotation keeps working. Measured: 0.7-1.0 s per rotation.
        self.warp_mode = self._resolve_warp_mode(disk)
        self.proxy_mode = self.warp_mode == "proxy"
        self.proxy_socks_port = env_int("OUC_PROXY_SOCKS_PORT",
                                        int(disk.get("proxy_socks_port", 40000)))
        self.proxy_http_port = env_int("OUC_PROXY_HTTP_PORT",
                                       int(disk.get("proxy_http_port", 8080)))
        self.http_proxy = os.environ.get(
            "OUC_HTTP_PROXY", f"http://127.0.0.1:{self.proxy_http_port}")
        self.bridge_pid_file = os.path.join(self.state_dir, "bridge.pid")

        if self.proxy_mode:
            # both are meaningless (and unsafe) without a TUN interface
            if self.kill_switch:
                self.kill_switch = False
                self.kill_switch_reason = "not applicable in proxy mode (no TUN)"
            if self.pin:
                self.pin = False

        self.probe_cmd = os.environ.get("OUC_PROBE_CMD")  # testing seam
        self.probe_url = os.environ.get("OUC_PROBE_URL", "https://api64.ipify.org")
        self.tun_iface = os.environ.get("OUC_TUN_IFACE", disk.get("tun_iface", "CloudflareWARP"))

        # paths that live under target_home
        self.opencode_log = os.environ.get("OUC_LOG_PATH") or os.path.join(
            self.target_home, ".local/share/opencode/log/opencode.log")
        self.user_state_dir = os.path.join(
            self.target_home, ".local/state", BRAND)

        # our own files
        self.state_file = os.path.join(self.state_dir, "state.json")
        self.state_lock = os.path.join(self.state_dir, "state.lock")
        self.rotation_lock = os.path.join(self.state_dir, "rotation.lock")
        self.pid_file = os.path.join(self.state_dir, "supervisor.pid")
        self.daemon_lock = os.path.join(self.state_dir, "supervisor.lock")
        # The single-instance lock lives outside the state dir on purpose: in a
        # container your state directory can disappear (rebuild, fresh image),
        # and a lock that disappears with it lets a second supervisor start.
        self.instance_lock = os.environ.get("OUC_INSTANCE_LOCK") or (
            "/run/opencode-unlimited.lock" if is_root and os.path.isdir("/run")
            else os.path.join(self.state_dir, "supervisor.lock"))
        self.supervisor_log = os.path.join(self.state_dir, "supervisor.log")
        self.hop_request = os.path.join(self.user_state_dir, "hop.request")
        self.hop_result = os.path.join(self.user_state_dir, "hop.result")
        # fallback channel for unprivileged users who cannot write into the
        # state dir (e.g. a root-owned ~/.local). Single-user machines only;
        # see SECURITY.md.
        self.hop_fallback_dir = "/tmp/opencode-unlimited"
        self.hop_fallback_request = os.path.join(self.hop_fallback_dir, "hop.request")
        self.hop_fallback_result = os.path.join(self.hop_fallback_dir, "hop.result")
        self.heartbeat = os.path.join(self.state_dir, "heartbeat")

    def _resolve_warp_mode(self, disk: dict) -> str:
        """warp (TUN) vs proxy (no TUN). Auto = trust reality, then override."""
        want = (os.environ.get("OUC_WARP_MODE") or disk.get("warp_mode") or "auto").lower()
        if want == "tun":          # documented name for a full tunnel
            want = "warp"
        if want in ("warp", "proxy"):
            return want
        # explicit "auto": a TUN device we can actually use means full mode
        if os.environ.get("CODESPACES", "").lower() in ("true", "1"):
            return "proxy"
        if os.geteuid() != 0:
            return "proxy"
        if not os.path.exists("/dev/net/tun"):
            return "proxy"
        if not (os.environ.get("OUC_IPTABLES") or which(
                "iptables", "/usr/sbin/iptables", "/sbin/iptables")):
            return "proxy"
        return "warp"

    def as_dict(self) -> dict:
        return {
            "version": VERSION,
            "mode": self.mode,
            "state_dir": self.state_dir,
            "target_home": self.target_home,
            "settle": self.settle,
            "cooldown": self.cooldown,
            "tick": self.tick,
            "egress_wait": self.egress_wait,
            "connect_wait": self.connect_wait,
            "reg_retries": self.reg_retries,
            "reg_backoff": self.reg_backoff,
            "rotation_timeout": self.rotation_timeout,
            "kill_switch": self.kill_switch,
            "kill_switch_reason": self.kill_switch_reason,
            "warp_bin": self.warp_bin,
            "iptables": self.iptables,
            "ip6tables": self.ip6tables,
            "opencode_bin": self.oc_bin,
            "opencode_log": self.opencode_log,
            "probe": self.probe_cmd or self.probe_url,
            "tun_iface": self.tun_iface,
            "warp_mode": self.warp_mode,
            "proxy_socks_port": self.proxy_socks_port,
            "proxy_http_port": self.proxy_http_port,
            "http_proxy": self.http_proxy,
            "pin": self.pin,
            "pin_hosts": self.pin_hosts,
            "hosts_file": self.hosts_file,
            "verify": self.verify,
            "test_model": self.test_model,
            "keys_attempts": self.keys_attempts,
            "rotation_mode": self.rotation_mode,
            "blacklist_ttl": self.blacklist_ttl,
        }


# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------

EMPTY_STATE = {
    "schema": 2,
    "offset": 0,
    "log_id": "",
    "tail_sig": "",
    "blacklist": [],
    "blacklist_ts": {},
    "hops": 0,
    "last_hop": 0,
    "last_wall": 0,
    "pending_wall": False,
    "pending_since": 0,
    "degraded": False,
    "current": "",
    "last_error": "",
    "last_hop_request": "",
    "updated": 0,
}


_LOCK_REGISTRY: dict[str, list] = {}
_LOCK_REGISTRY_GUARD = threading.RLock()


class StateLock:
    """flock-based mutex that is re-entrant *within this process*.

    flock() is per file descriptor, so a naive second acquisition inside an
    already-held critical section blocks forever — that is exactly the
    deadlock that froze the first supervisor build. Depth counting fixes it
    while still excluding other processes.
    """

    def __init__(self, path: str, blocking: bool = True):
        self.path = path
        self.blocking = blocking
        self.fh = None
        self.reentered = False

    def __enter__(self):
        with _LOCK_REGISTRY_GUARD:
            held = _LOCK_REGISTRY.get(self.path)
            if held is not None:
                held[1] += 1
                self.reentered = True
                return self
            mkdirp(os.path.dirname(self.path) or ".")
            fh = open(self.path, "a+")
            flags = fcntl.LOCK_EX if self.blocking else (fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fh.fileno(), flags)
            _LOCK_REGISTRY[self.path] = [fh, 1]
            self.fh = fh
            return self

    def __exit__(self, *exc):
        with _LOCK_REGISTRY_GUARD:
            if self.reentered:
                held = _LOCK_REGISTRY.get(self.path)
                if held:
                    held[1] -= 1
                return False
            held = _LOCK_REGISTRY.pop(self.path, None)
            if held:
                try:
                    fcntl.flock(held[0].fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
                try:
                    held[0].close()
                except Exception:
                    pass
            return False


class State:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def load(self) -> dict:
        data = read_json(self.cfg.state_file, None)
        if not isinstance(data, dict):
            data = dict(EMPTY_STATE)
        for k, v in EMPTY_STATE.items():
            data.setdefault(k, v)
        if not isinstance(data.get("blacklist"), list):
            data["blacklist"] = []
        if not isinstance(data.get("blacklist_ts"), dict):
            data["blacklist_ts"] = {}
        return data

    def save(self, data: dict) -> None:
        data["updated"] = now()
        data["schema"] = 2
        atomic_write_json(self.cfg.state_file, data)

    def update(self, **kwargs) -> dict:
        with StateLock(self.cfg.state_lock):
            d = self.load()
            d.update(kwargs)
            self.save(d)
            return d


# --------------------------------------------------------------------------
# IPv6 block helpers
# --------------------------------------------------------------------------

HEX_RE = re.compile(r"^[0-9a-fA-F:]+$")


def sync_current(cfg: "Config", state, log=None, egress: str = "",
                 why: str = "") -> str:
    """Keep state.json's `current` in step with the address WARP is really using.

    A rotation records the address it moved to — but WARP also changes the
    address on its own: a reconnect after a codespace restart, a key rotation
    done by hand, a tunnel re-established by `repair`. Without this the
    recorded block drifts away from the live egress and never catches up.
    Never fatal: a non-root `ouc status` may not be allowed to write state.
    """
    live = (egress or "").strip()
    if ":" not in live:
        try:
            live = (probe_egress(cfg) or "").strip()
        except Exception:
            return ""
    if ":" not in live:
        return ""
    try:
        data = state.load() if hasattr(state, "load") else dict(state or {})
    except Exception:
        return live
    old = (data.get("current") or "").strip()
    if old == live:
        return live
    data["current"] = live
    data["current_ts"] = now()
    data["current_source"] = why or "live egress"
    if old:
        data["last_drift"] = {"from": old, "to": live, "ts": now(),
                              "why": why or "live egress"}
    try:
        state.save(data) if hasattr(state, "save") else None
    except Exception:
        return live
    if log and old and norm64(old) != norm64(live):
        log(f"state: current address refreshed {old} -> {live} "
            f"({why or 'live egress'}) -- WARP moved without a rotation")
    elif log and old:
        log(f"state: current address refreshed {old} -> {live} "
            f"({why or 'live egress'})")
    return live


def norm64(addr: str) -> str:
    """/64-normalise an IPv6 address. Returns '' for anything unusable.

    This is the guard that keeps empty strings out of the blacklist.
    """
    if not addr:
        return ""
    addr = addr.strip()
    if not addr or not HEX_RE.match(addr):
        return ""
    if addr.count(":") == 3:
        # already a bare block ("2a09:bac5:b045:1caa") — the form we store in
        # the blacklist. Without this, stored blocks fail re-validation and
        # silently disappear on the next rotation.
        groups = addr.split(":")
        return addr if all(g and all(c in "0123456789abcdef" for c in g.lower())
                           for g in groups) else ""
    if addr.count(":") < 4:
        return ""
    parts = addr.split(":")
    if len(parts) < 4 or any(p == "" for p in parts[:4]):
        return ""
    block = ":".join(parts[:4])
    try:
        int(block.replace(":", ""), 16)
    except ValueError:
        return ""
    return block


# --------------------------------------------------------------------------
# WARP + probe
# --------------------------------------------------------------------------

class Pinner:
    """Point opencode at the tunnel's IPv6 /64 by pinning DNS, not by blocking IPs.

    Why this exists (measured on 2026-11-08): opencode reaches its backend over
    IPv4 whenever an A record exists, even with a healthy WARP IPv6 tunnel, and
    the free-tier quota is keyed on the source IP the backend sees. Rotating the
    WARP /64 therefore does nothing while opencode keeps using IPv4 — and WARP's
    IPv4 egress comes from a handful of shared NAT addresses that burn out.

    Adding AAAA entries to /etc/hosts makes opencode's resolver return only IPv6,
    so its traffic leaves through the tunnel and each fresh /64 is a fresh quota
    bucket. No firewall rules, no effect on any other program.
    """

    def __init__(self, cfg: Config, log: Log, state: "State | None" = None):
        self.cfg = cfg
        self.log = log
        self.state = state

    # -- resolution --------------------------------------------------------
    def resolve_aaaa(self) -> list[str]:
        """Fresh AAAA records, asked over DoH so our own pin cannot feed back."""
        fixed = self._fixed_aaaa()
        if fixed is not None:
            return [a for a in fixed if ":" in a]
        out: list[str] = []
        for host in self.cfg.pin_hosts:
            got = self._doh_aaaa(host)
            if not got:
                got = self._system_aaaa(host)
            out.extend(got)
        # stable order, no duplicates
        seen, uniq = set(), []
        for a in out:
            if a and a not in seen and ":" in a:
                seen.add(a)
                uniq.append(a)
        return uniq

    def _fixed_aaaa(self) -> list[str] | None:
        """`OUC_PIN_AAAA=addr1,addr2` skips the lookup entirely.

        Useful on networks that block DNS-over-HTTPS, and for tests. Set but
        empty means "there is no IPv6 here", which must not fall back to a
        lookup that would succeed and write a pin the network cannot use.
        """
        if "OUC_PIN_AAAA" not in os.environ:
            return None
        raw = os.environ.get("OUC_PIN_AAAA", "").strip()
        return [a.strip() for a in raw.split(",") if a.strip()]

    def _doh_aaaa(self, host: str) -> list[str]:
        url = f"https://cloudflare-dns.com/dns-query?name={host}&type=AAAA"
        try:
            req = urllib.request.Request(url, headers={"accept": "application/dns-json"})
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.loads(r.read().decode())
            return [a.get("data", "").strip() for a in data.get("Answer", [])
                    if a.get("type") == 28]
        except Exception:
            return []

    def _system_aaaa(self, host: str) -> list[str]:
        try:
            infos = socket.getaddrinfo(host, 443, socket.AF_INET6, socket.SOCK_STREAM)
        except OSError:
            return []
        return sorted({i[4][0] for i in infos})

    # -- /etc/hosts handling ----------------------------------------------
    def _read(self) -> str:
        try:
            with open(self.cfg.hosts_file, "r", errors="replace") as fh:
                return fh.read()
        except OSError:
            return ""

    @staticmethod
    def _strip_block(text: str) -> str:
        lines, skip = [], False
        for line in text.splitlines(True):
            if line.strip().startswith(PIN_BEGIN):
                skip = True
                continue
            if line.strip().startswith(PIN_END):
                skip = False
                continue
            if not skip:
                lines.append(line)
        return "".join(lines)

    def current_block(self) -> str:
        text = self._read()
        if PIN_BEGIN not in text:
            return ""
        out, grab = [], False
        for line in text.splitlines():
            if line.strip().startswith(PIN_BEGIN):
                grab = True
                continue
            if line.strip().startswith(PIN_END):
                break
            if grab:
                out.append(line)
        return "\n".join(out)

    def status(self) -> dict:
        want = self.resolve_aaaa() if not self.current_block() else \
            [l.split()[0] for l in self.current_block().splitlines() if l.strip()]
        return {"enabled": self.cfg.pin, "installed": PIN_BEGIN in self._read(),
                "hosts_file": self.cfg.hosts_file, "hosts": self.cfg.pin_hosts,
                "addresses": want}

    # -- install / remove --------------------------------------------------
    def install(self, addresses: list[str] | None = None) -> bool:
        if not self.cfg.pin:
            return False
        addrs = addresses or self.resolve_aaaa()
        if not addrs:
            self.log("pin: no IPv6 address for the opencode hosts — leaving DNS alone", "WARN")
            return False
        block = [PIN_BEGIN]
        for a in addrs:
            block.append(f"{a} {' '.join(self.cfg.pin_hosts)}")
        block.append(PIN_END)
        text = self._strip_block(self._read())
        if not text.endswith("\n"):
            text += "\n"
        new = text + "\n".join(block) + "\n"
        try:
            tmp = self.cfg.hosts_file + ".ouc-tmp"
            with open(tmp, "w") as fh:
                fh.write(new)
            os.replace(tmp, self.cfg.hosts_file)
        except OSError as exc:
            self.log(f"pin: cannot write {self.cfg.hosts_file}: {exc}", "ERROR")
            return False
        if self.state is not None:
            try:
                self.state.update(pin_addresses=addrs, pin_ts=now())
            except Exception:
                pass
        self.log(f"pinned {', '.join(self.cfg.pin_hosts)} to IPv6: {', '.join(addrs)}")
        return True

    def remove(self) -> bool:
        text = self._read()
        if PIN_BEGIN not in text:
            return False
        try:
            tmp = self.cfg.hosts_file + ".ouc-tmp"
            with open(tmp, "w") as fh:
                fh.write(self._strip_block(text))
            os.replace(tmp, self.cfg.hosts_file)
        except OSError as exc:
            self.log(f"pin: cannot clean {self.cfg.hosts_file}: {exc}", "ERROR")
            return False
        self.log("pin removed — opencode resolves normally again")
        return True


def ipv6_egress(cfg: Config) -> str:
    """The tunnel's public /64, or '' when there is no IPv6 path at all."""
    env = dict(os.environ, OUC_PROBE_IPV6="1")
    if cfg.probe_cmd:
        try:
            p = subprocess.run([cfg.probe_cmd], capture_output=True, text=True,
                               timeout=15, env=env)
            vals = [l.strip() for l in (p.stdout or "").splitlines() if l.strip()]
            for v in vals:
                if norm64(v):
                    return v
        except Exception:
            pass
        return ""
    proxy = ["-x", cfg.http_proxy] if cfg.proxy_mode else []
    try:
        p = subprocess.run(["curl", "-s", "-6", *proxy, "-m", "10", cfg.probe_url],
                           capture_output=True, text=True, timeout=20, env=env)
        if cfg.proxy_mode:
            # the proxy itself decides the family; -6 only applies locally
            p = subprocess.run(["curl", "-s", *proxy, "-m", "10", cfg.probe_url],
                               capture_output=True, text=True, timeout=20, env=env)
        return (p.stdout or "").strip()
    except Exception:
        return ""


VERIFY_PROMPT = "Reply with exactly: OUC-VERIFY-OK"


def _demote(cfg: Config):
    """When the supervisor runs as root, verification must run as the owner.

    root's $HOME has no opencode state, and `opencode run` there simply hangs
    instead of failing — which burned 50 s per rotation in testing.
    """
    if os.geteuid() != 0:
        return None
    try:
        st = os.stat(cfg.target_home)
    except OSError:
        return None
    uid, gid = st.st_uid, st.st_gid
    if uid == 0:
        return None
    try:
        import pwd
        name = pwd.getpwuid(uid).pw_name
    except Exception:
        name = str(uid)

    def apply():
        try:
            os.setgroups([])
        except OSError:
            pass
        os.setgid(gid)
        os.setuid(uid)

    return apply, uid, name


def _new_wall_since(path: str, offset: int) -> tuple[bool, int]:
    """Has a quota-wall signature been appended since `offset`?"""
    try:
        size = os.path.getsize(path)
    except OSError:
        return False, offset
    if size <= offset:
        return False, max(size, 0)
    try:
        with open(path, "rb") as fh:
            fh.seek(offset)
            chunk = fh.read(min(size - offset, 1 << 20))
    except OSError:
        return False, offset
    hit = any(p in chunk for p in WALL_PATTERNS)
    return hit, offset + len(chunk)


def _kill(proc: "subprocess.Popen") -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except (OSError, ProcessLookupError):
            return
        try:
            proc.wait(timeout=3)
            return
        except subprocess.TimeoutExpired:
            continue


def verify_request(cfg: Config, log: Log) -> dict:
    """Prove the quota really is usable again by making one real request.

    A rotation that only changes the egress address proves nothing: Cloudflare
    recycles /64s, so the "new" block may already be burned. This costs one
    request from the fresh bucket and is the only honest signal.

    A rate-limited block does not fail fast — opencode sits there streaming
    nothing — so we watch the log while the request runs and abort the moment a
    quota line appears instead of waiting for a timeout.
    """
    out = {"ran": False, "ok": False, "walled": False, "detail": ""}
    if not (cfg.verify and cfg.oc_bin and os.access(cfg.oc_bin, os.X_OK)):
        out["detail"] = "verification disabled or opencode not installed"
        return out

    env = dict(os.environ)
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    if cfg.proxy_mode:
        # opencode's fetch() speaks HTTP proxies; WARP only speaks SOCKS5,
        # so the request goes through the local bridge.
        env["HTTP_PROXY"] = env["HTTPS_PROXY"] = cfg.http_proxy
        env["http_proxy"] = env["https_proxy"] = cfg.http_proxy
        env.setdefault("NO_PROXY", "localhost,127.0.0.1,::1")
        env.setdefault("no_proxy", env["NO_PROXY"])
    preexec = None
    demoted = _demote(cfg)
    if demoted:
        preexec, _uid, name = demoted
        env["HOME"] = cfg.target_home
        env["USER"] = env["LOGNAME"] = name

    try:
        offset = os.path.getsize(cfg.opencode_log)
    except OSError:
        offset = 0

    out["ran"] = True
    tmp = None
    try:
        import tempfile
        fd, tmp = tempfile.mkstemp(prefix="ouc-verify-", suffix=".log")
        sink = os.fdopen(fd, "w+")
        proc = subprocess.Popen(
            [cfg.oc_bin, "run", "-m", cfg.test_model, VERIFY_PROMPT],
            stdout=sink, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            env=env, preexec_fn=preexec, start_new_session=True)
    except Exception as exc:
        out["detail"] = f"could not run opencode: {exc}"
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return out

    deadline = time.time() + cfg.verify_timeout
    killed = ""
    while True:
        if proc.poll() is not None:
            break
        walled, offset = _new_wall_since(cfg.opencode_log, offset)
        if walled:
            out["walled"] = True
            out["detail"] = "the new block is already rate-limited"
            killed = "wall"
            _kill(proc)
            break
        if time.time() > deadline:
            killed = "timeout"
            _kill(proc)
            break
        time.sleep(0.3)

    if not out["walled"]:
        # the wall line can land in the log microseconds before the process exits
        walled, offset = _new_wall_since(cfg.opencode_log, offset)
        if walled:
            out["walled"] = True
            out["detail"] = "the new block is already rate-limited"

    text = ""
    try:
        sink.flush()
        with open(tmp, "r", errors="replace") as fh:
            text = fh.read()
    except OSError:
        pass
    finally:
        try:
            sink.close()
        except Exception:
            pass
        try:
            os.unlink(tmp)
        except OSError:
            pass

    if out["walled"]:
        return out
    if "OUC-VERIFY-OK" in text:
        out["ok"] = True
        out["detail"] = "opencode answered on the new block"
        return out
    if killed == "timeout":
        out["detail"] = (f"no answer within {cfg.verify_timeout}s "
                         "(rate-limited blocks tend to hang)")
    else:
        out["detail"] = f"opencode exited without the marker: {text.strip()[-160:]!r}"
    return out


def _wall_count(path: str) -> int:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 2 * 1024 * 1024))
            chunk = fh.read()
        return sum(chunk.count(p) for p in WALL_PATTERNS)
    except OSError:
        return 0


class Warp:
    def __init__(self, cfg: Config, log: Log):
        self.cfg = cfg
        self.log = log

    def _run(self, *args, timeout: int = 25) -> tuple[int, str]:
        if not self.cfg.warp_bin:
            return 127, "warp-cli not found"
        cmds = ([self.cfg.warp_bin, "--accept-tos", *args], [self.cfg.warp_bin, *args])
        last = (1, "")
        for cmd in cmds:
            try:
                p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            except (OSError, subprocess.TimeoutExpired) as exc:
                last = (1, str(exc))
                continue
            out = (p.stdout or "") + (p.stderr or "")
            if p.returncode == 0:
                return 0, out.strip()
            last = (p.returncode, out.strip())
        return last

    def status(self) -> str:
        rc, out = self._run("status", timeout=10)
        return out

    def connected(self) -> bool:
        out = self.status()
        if not out:
            return False
        first = out.splitlines()[0]
        if "Connected" not in first:
            return False
        if self.cfg.proxy_mode:
            # proxy mode has no TUN interface to check; a listening SOCKS5 port
            # is the honest signal that the tunnel can actually carry traffic
            return port_open("127.0.0.1", self.cfg.proxy_socks_port)
        if os.environ.get("OUC_IGNORE_TUN_CHECK"):
            return True
        return tun_exists(self.cfg.tun_iface)

    def disconnect(self) -> None:
        self._run("disconnect", timeout=15)

    def registration_delete(self) -> None:
        self._run("registration", "delete", timeout=25)

    def registration_new(self) -> tuple[bool, str]:
        rc, out = self._run("registration", "new", timeout=30)
        return rc == 0 and "error" not in out.lower(), out

    def rotate_keys(self) -> tuple[bool, str]:
        """New WireGuard key pair, same registration.

        Measured: this yields a new public /64 in 2-4 s, versus ~17 s for
        delete+register, and it moves the address the backend sees.
        """
        rc, out = self._run("tunnel", "rotate-keys", timeout=25)
        return rc == 0, out

    def mode_warp(self) -> None:
        """Put WARP in the mode this deployment needs (never the wrong one)."""
        if self.cfg.proxy_mode:
            self._run("mode", "proxy", timeout=15)
            self._run("proxy", "port", str(self.cfg.proxy_socks_port), timeout=15)
            return
        self._run("mode", "warp", timeout=15)

    def connect(self) -> None:
        self._run("connect", timeout=20)


def port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def tun_exists(name: str) -> bool:
    try:
        return os.path.exists(f"/sys/class/net/{name}")
    except OSError:
        return False



# --------------------------------------------------------------------------
# proxy mode: SOCKS5 -> HTTP CONNECT bridge
# --------------------------------------------------------------------------

def _socks5_connect(socks_host: str, socks_port: int, host: str, port: int,
                    timeout: float = 20):
    """Minimal SOCKS5 CONNECT (no auth, remote DNS)."""
    sock = socket.create_connection((socks_host, socks_port), timeout=timeout)
    sock.sendall(b"\x05\x01\x00")
    if sock.recv(2) != b"\x05\x00":
        raise OSError("socks5 greeting refused")
    try:
        socket.inet_pton(socket.AF_INET, host)
        addr = b"\x01" + socket.inet_aton(host)
    except OSError:
        try:
            socket.inet_pton(socket.AF_INET6, host)
            addr = b"\x04" + socket.inet_pton(socket.AF_INET6, host)
        except OSError:
            raw = host.encode()[:255]
            addr = b"\x03" + bytes([len(raw)]) + raw
    sock.sendall(b"\x05\x01\x00" + addr + struct.pack("!H", port))
    rep = sock.recv(4)
    if len(rep) < 2 or rep[1] != 0:
        raise OSError("socks5 connect refused: %r" % (rep,))
    atyp = rep[3]
    if atyp == 1:
        sock.recv(6)
    elif atyp == 4:
        sock.recv(18)
    else:
        n = sock.recv(1)
        sock.recv((n[0] if n else 0) + 2)
    return sock


def _pump(a, b) -> None:
    try:
        while True:
            data = a.recv(65536)
            if not data:
                break
            b.sendall(data)
    except OSError:
        pass
    finally:
        for x in (a, b):
            try:
                x.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def run_bridge(listen: str, socks: str, log: "Log | None" = None) -> int:
    """HTTP CONNECT proxy in front of WARP's SOCKS5 port.

    opencode's fetch() understands HTTP proxies and rejects socks5:// outright
    ("UnsupportedProxyProtocol"), so this is the adapter that makes proxy mode
    usable by opencode. No dependencies, no root, binds to loopback only.
    """
    lh, lp = listen.rsplit(":", 1)
    sh, sp = socks.rsplit(":", 1)

    def handle(conn):
        try:
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                head += chunk
                if len(head) > 65536:
                    return
            line = head.split(b"\r\n", 1)[0].decode(errors="replace")
            parts = line.split(" ")
            if len(parts) < 2 or parts[0].upper() != "CONNECT":
                conn.sendall(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
                return
            host, _, port = parts[1].rpartition(":")
            upstream = _socks5_connect(sh, int(sp), host.strip("[]"), int(port))
            conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            threading.Thread(target=_pump, args=(upstream, conn), daemon=True).start()
            _pump(conn, upstream)
        except Exception as exc:  # a proxy must never die on one bad client
            try:
                conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            except OSError:
                pass
            if log:
                log(f"bridge: {exc}", "WARN")
        finally:
            try:
                conn.close()
            except OSError:
                pass

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind((lh, int(lp)))
    except OSError as exc:
        if log:
            log(f"bridge: cannot bind {listen}: {exc}", "ERROR")
        return 1
    srv.listen(64)
    if log:
        log(f"bridge listening on http://{listen} -> socks5://{socks}")
    while True:
        try:
            conn, _ = srv.accept()
        except OSError:
            continue
        threading.Thread(target=handle, args=(conn,), daemon=True).start()


class Bridge:
    """Keeps the HTTP->SOCKS5 bridge alive as a child process.

    It is deliberately a separate process: the supervisor restarts it, so a
    crash in a proxy thread can never take the quota watcher down with it.
    """

    def __init__(self, cfg: Config, log: Log):
        self.cfg = cfg
        self.log = log

    def alive(self) -> bool:
        return port_open("127.0.0.1", self.cfg.proxy_http_port)

    def ensure(self) -> None:
        if not self.cfg.proxy_mode:
            return
        if self.alive():
            return
        self.start()

    def start(self) -> bool:
        script = os.path.abspath(__file__)
        cmd = [sys.executable, script, "bridge",
               "--listen", f"127.0.0.1:{self.cfg.proxy_http_port}",
               "--socks", f"127.0.0.1:{self.cfg.proxy_socks_port}"]
        env = dict(os.environ, OUC_STATE_DIR=self.cfg.state_dir,
                   OUC_WARP_MODE="proxy")
        try:
            with open(os.path.join(self.cfg.state_dir, "bridge.log"), "a") as sink:
                proc = subprocess.Popen(cmd, stdout=sink, stderr=sink,
                                        stdin=subprocess.DEVNULL, env=env,
                                        start_new_session=True)
            mkdirp(self.cfg.state_dir)
            with open(self.cfg.bridge_pid_file, "w") as fh:
                fh.write(str(proc.pid))
        except Exception as exc:
            self.log(f"bridge: could not start: {exc}", "ERROR")
            return False
        for _ in range(20):
            time.sleep(0.25)
            if self.alive():
                self.log(f"bridge up on http://127.0.0.1:{self.cfg.proxy_http_port} "
                         f"-> socks5://127.0.0.1:{self.cfg.proxy_socks_port}")
                return True
        self.log("bridge: started but not accepting connections", "WARN")
        return False

    def stop(self) -> None:
        pid = read_pidfile(self.cfg.bridge_pid_file)
        if pid and pid_alive(pid):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        try:
            os.unlink(self.cfg.bridge_pid_file)
        except OSError:
            pass


def cmd_bridge(args, cfg: Config, log: Log) -> int:
    return run_bridge(args.listen or f"127.0.0.1:{cfg.proxy_http_port}",
                      args.socks or f"127.0.0.1:{cfg.proxy_socks_port}", log)


def probe_egress(cfg: Config) -> str:
    """Return the address the outside world sees (usually the WARP IPv6)."""
    if cfg.probe_cmd:
        try:
            p = subprocess.run(cfg.probe_cmd, shell=True, capture_output=True,
                               text=True, timeout=15)
            return (p.stdout or "").strip()
        except Exception:
            return ""
    proxy = ["-x", cfg.http_proxy] if cfg.proxy_mode else []
    try:
        p = subprocess.run(["curl", "-s", "-m", "8", *proxy, cfg.probe_url],
                           capture_output=True, text=True, timeout=15)
        if p.returncode == 0:
            return (p.stdout or "").strip()
    except Exception:
        pass
    # stdlib fallback
    try:
        import urllib.request
        if cfg.proxy_mode:
            handler = urllib.request.ProxyHandler(
                {"http": cfg.http_proxy, "https": cfg.http_proxy})
            opener = urllib.request.build_opener(handler)
            with opener.open(cfg.probe_url, timeout=10) as r:
                return r.read().decode().strip()
        with urllib.request.urlopen(cfg.probe_url, timeout=8) as r:
            return r.read().decode().strip()
    except Exception:
        return ""


# --------------------------------------------------------------------------
# firewall (leak guard)
# --------------------------------------------------------------------------

CHAIN = "OUC_LEAKGUARD"


class Firewall:
    """The kill-switch.

    Design rule learned the hard way: the guard must never be able to block its
    own repair. Every repair therefore *lifts* the jump rules first, and the
    rules are only re-installed once the tunnel is verified up. If the repair
    fails we stay lifted (degraded) — the machine keeps working, and we say so.
    """

    def __init__(self, cfg: Config, log: Log, state: "State | None" = None):
        self.cfg = cfg
        self.log = log
        self.state = state

    def _record(self, installed: bool) -> None:
        """Persist what we did, so an unprivileged `status` can tell the truth."""
        if self.state is None:
            return
        try:
            self.state.update(guard_installed=installed)
        except Exception:
            pass

    # -- availability ------------------------------------------------------
    def available(self) -> tuple[bool, str]:
        if not self.cfg.kill_switch:
            return False, self.cfg.kill_switch_reason
        if not self.cfg.iptables or not self.cfg.ip6tables:
            return False, "iptables/ip6tables not found"
        if not os.path.exists("/dev/net/tun") and not env_bool("OUC_IGNORE_TUN_CHECK"):
            return False, "/dev/net/tun missing"
        return True, "ok"

    def _ipt(self, v6: bool, *args, timeout: int = 10) -> tuple[int, str]:
        exe = self.cfg.ip6tables if v6 else self.cfg.iptables
        if not exe:
            return 127, "missing"
        try:
            p = subprocess.run([exe, *args], capture_output=True, text=True,
                               timeout=timeout)
            return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()
        except Exception as exc:
            return 1, str(exc)

    def _rules_v4(self) -> list[list[str]]:
        iface = self.cfg.tun_iface
        return [
            ["-A", CHAIN, "-o", "lo", "-j", "ACCEPT"],
            ["-A", CHAIN, "-m", "state", "--state", "ESTABLISHED,RELATED", "-j", "ACCEPT"],
            ["-A", CHAIN, "-d", "10.0.0.0/8", "-j", "ACCEPT"],
            ["-A", CHAIN, "-d", "172.16.0.0/12", "-j", "ACCEPT"],
            ["-A", CHAIN, "-d", "192.168.0.0/16", "-j", "ACCEPT"],
            # Cloudflare WARP tunnel endpoints (needed to keep/repair the tunnel)
            ["-A", CHAIN, "-d", "162.159.0.0/16", "-j", "ACCEPT"],
            ["-A", CHAIN, "-d", "188.114.0.0/16", "-j", "ACCEPT"],
            ["-A", CHAIN, "-o", iface, "-j", "ACCEPT"],
            ["-A", CHAIN, "-j", "DROP"],
        ]

    def _rules_v6(self) -> list[list[str]]:
        iface = self.cfg.tun_iface
        return [
            ["-A", CHAIN, "-o", "lo", "-j", "ACCEPT"],
            ["-A", CHAIN, "-m", "state", "--state", "ESTABLISHED,RELATED", "-j", "ACCEPT"],
            ["-A", CHAIN, "-d", "fe80::/10", "-j", "ACCEPT"],
            ["-A", CHAIN, "-d", "2606:4700::/32", "-j", "ACCEPT"],
            ["-A", CHAIN, "-o", iface, "-j", "ACCEPT"],
            ["-A", CHAIN, "-j", "DROP"],
        ]

    def installed(self) -> bool:
        if self.cfg.proxy_mode:
            return False        # there is no guard in proxy mode, and no probe
        ok4, _ = self._ipt(False, "-C", "OUTPUT", "-j", CHAIN)
        ok6, _ = self._ipt(True, "-C", "OUTPUT", "-j", CHAIN)
        return ok4 == 0 and ok6 == 0

    def install(self) -> bool:
        avail, why = self.available()
        if not avail:
            self.log(f"kill-switch not installed: {why}", "WARN")
            return False
        for v6, rules in ((False, self._rules_v4()), (True, self._rules_v6())):
            self._ipt(v6, "-N", CHAIN)
            self._ipt(v6, "-F", CHAIN)
            for rule in rules:
                self._ipt(v6, *rule)
            rc, out = self._ipt(v6, "-C", "OUTPUT", "-j", CHAIN)
            if rc != 0:
                self._ipt(v6, "-I", "OUTPUT", "-j", CHAIN)
        self._record(True)
        self.log("kill-switch installed (v4+v6)")
        return True

    def lift(self) -> None:
        """Remove the OUTPUT jumps. The (empty-ish) chain is kept."""
        self._record(False)
        for v6 in (False, True):
            for _ in range(5):  # tolerate duplicates left by older versions
                rc, _ = self._ipt(v6, "-D", "OUTPUT", "-j", CHAIN)
                if rc != 0:
                    break
        self.log("kill-switch lifted (traffic goes out on the real interface)")

    def teardown(self) -> None:
        for v6 in (False, True):
            for _ in range(5):
                rc, _ = self._ipt(v6, "-D", "OUTPUT", "-j", CHAIN)
                if rc != 0:
                    break
            self._ipt(v6, "-F", CHAIN)
            self._ipt(v6, "-X", CHAIN)
        self.log("kill-switch removed")


# --------------------------------------------------------------------------
# core: rotation
# --------------------------------------------------------------------------

class RotationError(RuntimeError):
    pass


def _spent_blocks(cfg: Config, state: State) -> set[str]:
    """Blocks burned recently enough that they are still rate-limited.

    Measured window is roughly 4 hours, so the default TTL is 4 h rather than a
    day: a block that has aged out is a legitimate candidate again.
    """
    st = state.load()
    out: set[str] = set()
    ts = st.get("blacklist_ts") or {}
    for b in st.get("blacklist") or []:
        nb = norm64(b)
        if not nb:
            continue
        when = int(ts.get(nb) or ts.get(b) or 0)
        if when and now() - when >= cfg.blacklist_ttl:
            continue                        # old enough to be worth another try
        out.add(nb)
    return out


def _wait_for_egress(cfg: Config, warp: "Warp", old64: str, t0: float) -> str:
    """Wait for the tunnel to come back with an IPv6 egress that differs."""
    deadline = time.time() + min(cfg.connect_wait + cfg.egress_wait,
                                 max(8, cfg.rotation_timeout - (time.time() - t0)))
    while time.time() < deadline:
        time.sleep(1.5)
        if not warp.connected():
            continue
        for _ in range(2):
            cand = probe_egress(cfg) or ipv6_egress(cfg)
            if norm64(cand):
                return cand
            time.sleep(1)
    return ""


def rotate(cfg: Config, log: Log, state: State, why: str) -> dict:
    """Get a fresh IPv6 /64 that actually works, and prove it.

    Order (v2.1):
      1. make sure opencode is pinned to IPv6 — otherwise the rotation buys nothing
      2. fast path: `warp-cli tunnel rotate-keys` (2-4 s) and verify
      3. fallback: full re-registration (delete -> settle -> new) and verify
      4. give up gracefully: keep the tunnel up, stay online, say what happened

    "Success" now means a real request succeeded on the new block, not that the
    egress address changed — Cloudflare recycles /64s, so a different address is
    not the same thing as fresh quota.
    """
    t0 = time.time()
    fw = Firewall(cfg, log, state)
    warp = Warp(cfg, log)
    pinner = Pinner(cfg, log, state)
    result: dict = {"ok": False, "old": "", "new": "", "duration": 0.0, "why": why,
                    "error": "", "degraded": False, "how": "", "verified": False,
                    "attempts": []}

    try:
        lock_fh = open(cfg.rotation_lock, "a+")
    except OSError as exc:
        result["error"] = f"cannot open rotation lock: {exc}"
        return result
    try:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_fh.close()
        result["error"] = "rotation already in progress"
        return result

    def try_candidate(how: str, cand: str, old64: str, spent: set[str]) -> bool:
        cand64 = norm64(cand)
        entry = {"how": how, "block": cand64, "accepted": False}
        result["attempts"].append(entry)
        if not cand64:
            entry["reason"] = "no IPv6 egress"
            log(f"{how}: no IPv6 egress after the change", "WARN")
            return False
        if cand64 == old64:
            entry["reason"] = "same /64"
            log(f"{how}: still on {cand64} — asking again", "WARN")
            return False
        if cand64 in spent:
            entry["reason"] = "already exhausted"
            log(f"{how}: {cand64} is in our spent list (already exhausted) — "
                "trying again", "WARN")
            return False
        v = verify_request(cfg, log)
        entry["verify"] = v
        if v.get("walled"):
            entry["reason"] = "verification hit the quota wall"
            log(f"{how}: {cand64} did not restore the quota "
                f"({v.get('detail')}) — rotating again", "WARN")
            return False
        if v.get("ok"):
            result["verified"] = True
        elif v.get("ran"):
            # a rate-limited block tends to hang, and a hang is not proof of
            # anything. Rotating again costs ~3 s; handing over a dead block
            # costs the user a frozen request. Always spend the extra 3 s.
            entry["reason"] = f"could not be confirmed: {v.get('detail')}"
            log(f"{how}: {cand64} could not be confirmed with a request "
                f"({v.get('detail')}) — trying another block", "WARN")
            result["_fallback"] = cand
            return False
        else:
            log(f"{how}: {cand64} accepted without a verification request "
                f"({v.get('detail')})", "INFO")
        result["new"] = cand
        result["how"] = how
        result["ok"] = True
        entry["accepted"] = True
        return True

    try:
        if cfg.proxy_mode:
            warp.mode_warp()               # proxy mode must be the active one
            Bridge(cfg, log).ensure()      # a rotation is invisible without it
        old = probe_egress(cfg)
        old64 = norm64(old)
        result["old"] = old
        log(f"rotation start ({why}); egress={old or 'unreachable'}")

        # 0. the whole point of rotating a /64 is that opencode uses IPv6
        if cfg.pin and not cfg.proxy_mode:
            pinner.install()

        if cfg.proxy_mode:
            # no TUN, no guard, nothing to lift: the proxy carries the traffic
            p = Bridge(cfg, log)
            p.ensure()
            was_installed = False
            if not p.alive():
                log("bridge is not running — a rotation would be invisible to "
                    "opencode; starting it", "WARN")
        else:
            was_installed = fw.installed()
            if was_installed:
                fw.lift()
            state.update(degraded=bool(was_installed))

        spent = _spent_blocks(cfg, state)

        # ---- phase 1: rotate keys (fast) ---------------------------------
        if cfg.rotation_mode in ("auto", "keys"):
            for i in range(1, cfg.keys_attempts + 1):
                if time.time() - t0 > cfg.rotation_timeout:
                    log("rotation budget exhausted during key rotation", "WARN")
                    break
                ok, out = warp.rotate_keys()
                if not ok:
                    log(f"rotate-keys failed: {out[:120]!r} — falling back", "WARN")
                    break
                cand = _wait_for_egress(cfg, warp, old64, t0)
                if try_candidate(f"rotate-keys#{i}", cand, old64, spent):
                    break

        # ---- phase 2: full re-registration (slower, different pool) ------
        if not result["ok"] and cfg.rotation_mode == "register":
            log("using the registration path (OUC_ROTATION_MODE=register)")
        if not result["ok"] and cfg.rotation_mode in ("auto", "register"):
            for cycle in range(1, cfg.reg_retries + 1):
                if time.time() - t0 > cfg.rotation_timeout:
                    log("rotation budget exhausted during re-registration", "WARN")
                    break
                warp.disconnect()
                warp.registration_delete()
                log(f"waiting {cfg.settle}s for the old registration to clear")
                time.sleep(cfg.settle)
                reg_ok = False
                for attempt in range(1, cfg.reg_retries + 1):
                    ok, out = warp.registration_new()
                    if ok:
                        reg_ok = True
                        log(f"registration new: ok (attempt {attempt}, cycle {cycle})")
                        break
                    if attempt < cfg.reg_retries:
                        delay = cfg.reg_backoff * (2 ** (attempt - 1))
                        log(f"registration new failed: {out[:120]!r} — retry in {delay}s", "WARN")
                        time.sleep(delay)
                if not reg_ok:
                    log("could not register a new device", "ERROR")
                    continue
                warp.mode_warp()
                warp.connect()
                cand = _wait_for_egress(cfg, warp, old64, t0)
                if try_candidate(f"re-register#{cycle}", cand, old64, spent):
                    break

        if not result["ok"] and result.get("_fallback"):
            # nothing could be confirmed: keep the last untested block rather
            # than leaving the user with an unrotated, definitely-walled one.
            cand = result.pop("_fallback")
            result["ok"] = True
            result["new"] = cand
            result["how"] = "unconfirmed"
            result["verified"] = False
            log(f"no block could be confirmed by a real request; keeping "
                f"{norm64(cand)}. If a request hangs, run: ouc hop", "WARN")

        if not result["ok"]:
            tried = ", ".join(f"{a['how']}={a['block'] or '-'} ({a.get('reason','')})"
                              for a in result["attempts"]) or "nothing"
            result["error"] = ("no fresh /64 available right now; every candidate was "
                               f"spent or unverifiable [{tried}]")
            raise RotationError(result["error"])

        # 5. success: record it (with timestamps) and close the gate again
        result.pop("_fallback", None)
        st = state.load()
        blacklist = [norm64(b) for b in st.get("blacklist", []) if norm64(b)]
        ts_map = {norm64(k): int(v) for k, v in (st.get("blacklist_ts") or {}).items()
                  if norm64(k)}
        if old64:
            if old64 not in blacklist:
                blacklist.append(old64)
            ts_map[old64] = now()
        blacklist = blacklist[-200:]
        result["duration"] = round(time.time() - t0, 2)
        state.update(blacklist=blacklist, blacklist_ts=ts_map,
                     last_block_reused=False,
                     current=result["new"], hops=int(st.get("hops", 0)) + 1,
                     last_hop=now(), pending_wall=False, pending_since=0,
                     last_error="", degraded=False, last_rotation=result)
        if cfg.kill_switch and not cfg.proxy_mode:
            if fw.install():
                log("kill-switch re-installed")
            else:
                log("kill-switch could not be re-installed (tunnel is up)", "WARN")
        log(f"rotation done in {result['duration']}s via {result['how']}: "
            f"{old64 or '?'} -> {norm64(result['new'])}"
            + ("" if not result["verified"] else " (verified with a real request)"))
        return result

    except RotationError as exc:
        result["error"] = str(exc)
    except Exception as exc:  # never let a rotation crash the supervisor
        result["error"] = f"unexpected: {exc}"
        try:
            log(f"rotation raised {type(exc).__name__}: {exc}\n"
                + traceback.format_exc(), "ERROR")
        except Exception:
            pass
    finally:
        el = time.time() - t0
        result["duration"] = round(el, 2)
        if not result["ok"]:
            log(f"rotation FAILED after {result['duration']}s: {result['error']}", "ERROR")
            tunnel_up = Warp(cfg, log).connected()
            if cfg.kill_switch and not cfg.proxy_mode:
                if tunnel_up:
                    if was_installed and fw.install():
                        log("tunnel is up — kill-switch restored")
                    state.update(degraded=False, last_error=result["error"],
                                 last_rotation=result)
                else:
                    if fw.installed():
                        fw.lift()
                    result["degraded"] = True
                    state.update(degraded=True, last_error=result["error"],
                                 pending_wall=True, pending_since=now(),
                                 last_rotation=result)
                    log("kill-switch LIFTED: no tunnel, so the machine keeps its "
                        "internet. The real IP IS VISIBLE until the tunnel returns.",
                        "SECURITY")
            else:
                state.update(last_error=result["error"], last_rotation=result)
        try:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
            lock_fh.close()
        except Exception:
            pass
    return result


def scan_wall(cfg: Config, state: State, log: Log, baseline: bool = False) -> dict:
    """One idempotent detection pass over opencode's log.

    Idempotency: we always advance the stored byte offset to the end of what we
    read, and remember the log's identity (dev/inode). Re-running the scan over
    the same bytes therefore cannot trigger twice, and a rotated/truncated log
    resets the offset instead of mis-reading.
    """
    path = cfg.opencode_log
    out = {"checked": False, "wall": False, "action": "none", "detail": ""}
    try:
        st = os.stat(path)
    except OSError:
        return out
    out["checked"] = True

    with StateLock(cfg.state_lock):
        s = state.load()
        log_id = f"{st.st_dev}:{st.st_ino}"
        offset = int(s.get("offset") or 0)
        if baseline and s.get("log_id") != log_id:
            # first pass after a (re)start: everything already in the log was
            # written before we existed, so it must not trigger a rotation.
            try:
                with open(path, "rb") as fh:
                    fh.seek(max(0, st.st_size - 64))
                    tail = fh.read(st.st_size - max(0, st.st_size - 64))
                sig = hashlib.sha256(tail).hexdigest()
            except OSError:
                sig = ""
            state.save({**s, "log_id": log_id, "offset": st.st_size, "tail_sig": sig})
            out["detail"] = f"baselined at {st.st_size} bytes (history ignored)"
            return out
        if s.get("log_id") != log_id or st.st_size < offset:
            offset = 0                     # new or rotated log
        elif offset and s.get("tail_sig"):
            # same file, plausible size: verify the bytes before the offset are
            # still the ones we consumed. Catches truncate+rewrite to the same
            # length, which a size check alone cannot see.
            try:
                with open(path, "rb") as fh:
                    fh.seek(max(0, offset - 64))
                    tail = fh.read(offset - max(0, offset - 64))
                if hashlib.sha256(tail).hexdigest() != s["tail_sig"]:
                    offset = 0
            except OSError:
                offset = 0
        if st.st_size == offset:
            state.save({**s, "log_id": log_id, "offset": offset})
            out["detail"] = "no new bytes"
            return out

        try:
            with open(path, "rb") as fh:
                fh.seek(offset)
                chunk = fh.read(min(MAX_LOG_READ, st.st_size - offset))
        except OSError as exc:
            out["detail"] = f"read failed: {exc}"
            return out

        new_offset = offset + len(chunk)
        hit = next((p.decode() for p in WALL_PATTERNS if p in chunk), "")
        try:
            with open(path, "rb") as fh:
                fh.seek(max(0, new_offset - 64))
                tail = fh.read(new_offset - max(0, new_offset - 64))
            sig = hashlib.sha256(tail).hexdigest()
        except OSError:
            sig = ""
        s.update({"log_id": log_id, "offset": new_offset, "tail_sig": sig})
        state.save(s)

        if not hit:
            out["detail"] = f"{len(chunk)} new bytes, no quota signature"
            return out

        out["wall"] = True
        out["detail"] = f"quota signature {hit!r} found"
        state.update(last_wall=now(), pending_wall=True, pending_since=now())
        log(f"quota wall detected ({hit})", "WARN")
        out["action"] = "detected"
        return out


def maybe_rotate_for_wall(cfg: Config, state: State, log: Log) -> dict | None:
    """Rotate if a detected wall is pending and the cooldown has expired."""
    with StateLock(cfg.state_lock):
        s = state.load()
        if not s.get("pending_wall"):
            return None
        age = now() - int(s.get("last_hop") or 0)
        if age < cfg.cooldown:
            log(f"wall pending, waiting out cooldown ({age}s < {cfg.cooldown}s)")
            return {"ok": False, "skipped": "cooldown", "wait": cfg.cooldown - age}
    return rotate(cfg, log, state, why="quota-wall")


# --------------------------------------------------------------------------
# supervisor
# --------------------------------------------------------------------------

class Supervisor:
    def __init__(self, cfg: Config, log: Log):
        self.cfg = cfg
        self.log = log
        self.state = State(cfg)
        self.fw = Firewall(cfg, log, self.state)
        self.pinner = Pinner(cfg, log, self.state)
        self.bridge = Bridge(cfg, log)
        self.warp = Warp(cfg, log)
        self.stop = False
        self.last_request = ""
        self.last_repair = 0
        self._was_connected = None      # for reconnect-edge detection
        self._addr_checked = 0.0        # last live-egress check

    ADDRESS_CHECK_SECS = 60

    def sync_address(self) -> None:
        """Refresh state['current'] when WARP is using a different address.

        Cheap on purpose: it probes the live egress only when the tunnel has
        just come back (a restart, a reconnect) or every ADDRESS_CHECK_SECS.
        """
        try:
            conn = self.warp.connected()
        except Exception:
            return
        edge = bool(conn) and self._was_connected is False
        self._was_connected = conn
        if not conn:
            return
        due = (time.time() - self._addr_checked) >= self.ADDRESS_CHECK_SECS
        if not (edge or due):
            return
        self._addr_checked = time.time()
        try:
            sync_current(self.cfg, self.state, self.log,
                         why="warp reconnected" if edge else "periodic check")
        except Exception as exc:
            self.log(f"address sync skipped: {exc}", "WARN")

    def heartbeat(self, note: str = "") -> None:
        try:
            atomic_write_json(self.cfg.heartbeat,
                              {"pid": os.getpid(), "ts": now(), "note": note})
        except OSError:
            pass

    def ensure_pin(self, tunnel_up: bool) -> None:
        """Keep opencode on the IPv6 path — but never break it when there is none.

        Pinning is only useful while the tunnel carries IPv6; if the tunnel is
        down the pin is removed so opencode keeps working over IPv4.
        """
        if not self.cfg.pin or self.cfg.proxy_mode:
            # proxy mode: every opencode request already leaves through WARP,
            # and there is no local IPv6 route to pin to
            return
        if not tunnel_up:
            if self.pinner.current_block():
                self.pinner.remove()
            return
        st = self.state.load()
        stale = now() - int(st.get("pin_ts") or 0) > self.cfg.pin_refresh
        if not self.pinner.current_block() or stale:
            self.pinner.install()

    def ensure_tunnel(self) -> None:
        """Keep the tunnel up — and never deadlock trying."""
        if self.warp.connected():
            if self.state.load().get("degraded") and self.cfg.kill_switch:
                if self.fw.install():
                    self.state.update(degraded=False)
                    self.log("tunnel is back — kill-switch restored")
            self.ensure_pin(True)
            return
        self.ensure_pin(False)
        if now() - self.last_repair < 15:
            return
        self.last_repair = now()
        self.log("tunnel is down — repairing (kill-switch opens during repair)", "WARN")
        if self.fw.installed():
            self.fw.lift()
        self.state.update(degraded=self.cfg.kill_switch)
        for attempt in range(1, 3):
            self.warp.mode_warp()          # proxy mode re-applies proxy+port
            self.warp.connect()
            for _ in range(int(self.cfg.connect_wait / 2)):
                time.sleep(2)
                if self.warp.connected():
                    self.log(f"tunnel repaired ({self.cfg.warp_mode} mode)")
                    if self.cfg.kill_switch and self.fw.install():
                        self.state.update(degraded=False)
                    return
            self.log(f"reconnect attempt {attempt} failed", "WARN")
        # last resort: full rotation (fresh registration)
        res = rotate(self.cfg, self.log, self.state, why="tunnel-down")
        if not res.get("ok"):
            self.log("tunnel repair failed — staying open (real IP visible) and "
                     "retrying next tick", "SECURITY")

    def enforce_firewall(self) -> None:
        if self.cfg.proxy_mode:
            return
        if not self.cfg.kill_switch:
            return
        if not self.warp.connected():
            if self.fw.installed():
                self.fw.lift()
                self.log("tunnel down — kill-switch lifted", "WARN")
            return
        if not self.fw.installed():
            self.fw.install()
            self.state.update(degraded=False)

    def handle_request(self) -> None:
        req, req_path = None, ""
        for path in (self.cfg.hop_request, self.cfg.hop_fallback_request):
            r = read_json(path, None)
            if isinstance(r, dict) and str(r.get("id") or "") != self.last_request:
                req, req_path = r, path
                break
        if not isinstance(req, dict):
            return
        rid = str(req.get("id") or "")
        if not rid or rid == self.last_request:
            return
        self.last_request = rid
        self.log(f"hop request {rid} from {req.get('user', '?')}")
        res = rotate(self.cfg, self.log, self.state, why="manual-hop")
        payload = {"id": rid, "ok": bool(res.get("ok")), "old": res.get("old", ""),
                   "new": res.get("new", ""), "duration": res.get("duration", 0),
                   "error": res.get("error", ""), "ts": now()}
        result_path = (self.cfg.hop_fallback_result if req_path == self.cfg.hop_fallback_request
                       else self.cfg.hop_result)
        for pth in {result_path, self.cfg.hop_result, self.cfg.hop_fallback_result}:
            try:
                mkdirp(os.path.dirname(pth), 0o755)
                atomic_write_json(pth, payload, mode=0o644)
                os.chmod(pth, 0o644)
            except OSError:
                pass
        self.state.update(last_hop_request=rid)

    def tick(self, baseline: bool = False) -> None:
        if self.cfg.proxy_mode:
            self.bridge.ensure()
        self.ensure_tunnel()
        self.sync_address()            # WARP may have moved us on its own
        self.ensure_pin(self.warp.connected())
        self.enforce_firewall()
        scan_wall(self.cfg, self.state, self.log, baseline=baseline)
        maybe_rotate_for_wall(self.cfg, self.state, self.log)

        supervisor_auto_resume(self.cfg, self.state, self.log)
        self.handle_request()
        self.heartbeat()

    def run_forever(self) -> None:
        def _stop(signum, frame):
            self.stop = True

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)
        self.log(f"supervisor up (pid {os.getpid()}, mode {self.cfg.mode}, "
                 f"warp {self.cfg.warp_mode}, kill-switch {self.cfg.kill_switch_reason})")
        # startup safety: if the guard is installed but the tunnel is not up,
        # open the gate *before* doing anything else.
        if self.cfg.kill_switch and self.fw.installed() and not self.warp.connected():
            self.fw.lift()
            self.log("startup: guard was installed without a tunnel — lifted it first",
                     "SECURITY")
        if self.cfg.proxy_mode:
            self.warp.mode_warp()      # proxy + SOCKS port, in case it drifted
            self.bridge.ensure()
            self.log(f"proxy mode: opencode must use HTTP_PROXY={self.cfg.http_proxy}")
        self.ensure_pin(self.warp.connected())
        self.heartbeat()
        first = True
        while not self.stop:
            try:
                self.tick(baseline=first)
                first = False
            except Exception as exc:
                self.log(f"tick error: {exc}", "ERROR")
            for _ in range(self.cfg.tick * 10):
                if self.stop:
                    break
                time.sleep(0.1)
        self.log("supervisor stopping — removing kill-switch so nothing is stranded")
        try:
            self.fw.teardown()
        except Exception:
            pass
        try:
            self.pinner.remove()
        except Exception:
            pass
        try:
            if self.cfg.proxy_mode:
                self.bridge.stop()
        except Exception:
            pass
        self.heartbeat(note="stopped")


# --------------------------------------------------------------------------
# portable (no systemd) plumbing
# --------------------------------------------------------------------------

def guard_state(cfg: "Config", state, log: "Log") -> tuple[bool, bool]:
    """Return (installed, verified).

    Only root can ask iptables. A normal user must not be told the guard is
    missing just because the kernel refused to answer, so fall back to what the
    supervisor last recorded in state.json.
    """
    data = state.load() if hasattr(state, "load") else (state or {})
    if os.geteuid() == 0:
        return Firewall(cfg, log).installed(), True
    return bool(data.get("guard_installed")), False


def unit_active() -> bool:
    if not systemd_available():
        return False
    rc, out = systemctl("is-active", UNIT_NAME)
    return out.strip() == "active"


def supervisor_alive(cfg: "Config") -> tuple[bool, int, str]:
    """Alive = the systemd unit is active, or a portable daemon answers."""
    if cfg.mode == "systemd" and unit_active():
        try:
            rc, out = systemctl("show", "-p", "MainPID", "--value", UNIT_NAME)
            pid = int(out.strip() or 0)
        except Exception:
            pid = 0
        return True, pid, "systemd unit active"
    alive, pid, why = supervisor_running(cfg)
    return alive, pid, why


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno == errno.EPERM
    return True


def read_pidfile(path: str) -> int:
    try:
        with open(path) as fh:
            return int((fh.read() or "0").split()[0])
    except Exception:
        return 0


def supervisor_running(cfg: Config) -> tuple[bool, int, str]:
    """Return (alive, pid, reason). Alive means PID file valid AND heartbeat fresh."""
    pid = read_pidfile(cfg.pid_file)
    if not pid or not pid_alive(pid):
        return False, pid, "no live process"
    hb = read_json(cfg.heartbeat, {}) or {}
    age = now() - int(hb.get("ts") or 0)
    if age > max(30, cfg.tick * 8):
        return False, pid, f"heartbeat stale ({age}s)"
    return True, pid, "ok"


def daemonize(cfg: Config, argv: list[str]) -> int:
    """Classic double-fork daemon with PID file + flock so we can never double-run."""
    mkdirp(cfg.state_dir)
    if os.fork() > 0:
        # parent waits for the child to publish a heartbeat
        for _ in range(100):
            time.sleep(0.1)
            alive, pid, _ = supervisor_running(cfg)
            if alive:
                print(f"{BRAND} supervisor started (pid {pid})")
                return 0
        print(f"{BRAND} supervisor failed to start — see {cfg.supervisor_log}",
              file=sys.stderr)
        return 1
    os.setsid()
    if os.fork() > 0:
        os._exit(0)
    os.chdir("/")
    # hold an exclusive lock for the whole lifetime of the daemon
    try:
        mkdirp(os.path.dirname(cfg.instance_lock) or ".")
        lock_fh = open(cfg.instance_lock, "a+")
    except OSError:
        lock_fh = open(cfg.daemon_lock, "a+")
    try:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.stderr.write("another supervisor already holds the lock\n")
        os._exit(1)
    # stdio -> supervisor log
    mkdirp(os.path.dirname(cfg.supervisor_log))
    fd = os.open(cfg.supervisor_log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.dup2(fd, 0)
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    if fd > 2:
        os.close(fd)
    with open(cfg.pid_file, "w") as fh:
        fh.write(f"{os.getpid()}\n")
    os.chmod(cfg.pid_file, 0o644)
    log = Log(cfg.supervisor_log, echo=False)
    try:
        Supervisor(cfg, log).run_forever()
    finally:
        try:
            os.unlink(cfg.pid_file)
        except OSError:
            pass
    os._exit(0)


# --------------------------------------------------------------------------
# systemd helpers
# --------------------------------------------------------------------------

def systemd_available() -> bool:
    return bool(shutil.which("systemctl")) and os.path.isdir("/run/systemd/system")


def systemctl(*args, timeout: int = 60) -> tuple[int, str]:
    try:
        p = subprocess.run(["systemctl", *args], capture_output=True, text=True,
                           timeout=timeout)
        return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()
    except Exception as exc:
        return 1, str(exc)


UNIT_TEMPLATE = """[Unit]
Description=opencode-unlimited supervisor (WARP keepalive, quota rotation, leak guard)
After=network-online.target warp-svc.service
Wants=network-online.target

[Service]
Type=simple
ExecStart={bin} supervise --foreground
Environment=OUC_STATE_DIR={state_dir}
Environment=OUC_TARGET_HOME={home}
Restart=always
RestartSec=10
# never let a crash leave the firewall installed with no tunnel
ExecStopPost=-{bin} killswitch off

[Install]
WantedBy=multi-user.target
"""


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_install(args, cfg: Config, log: Log) -> int:
    root = os.geteuid() == 0
    if not root and not args.assume_root:
        print("install needs root (run via sudo, or use install.sh)", file=sys.stderr)
        return 1
    mkdirp(cfg.state_dir, 0o755)
    for p in (cfg.state_dir, os.path.join(cfg.state_dir, "archive")):
        try:
            os.chmod(p, 0o755)
        except OSError:
            pass

    # config on disk: target home, mode, kill-switch decision
    avail, why = Firewall(cfg, log).available()
    kill_switch = avail and not env_bool("OUC_DISABLE_KILL_SWITCH", False)
    mode = "systemd" if (systemd_available() and not args.portable) else "portable"
    if args.portable:
        mode = "portable"
    atomic_write_json(cfg.config_file, {
        "version": VERSION,
        "mode": mode,
        "target_home": cfg.target_home,
        "kill_switch": kill_switch,
        "kill_switch_note": why,
        "settle": cfg.settle,
        "cooldown": cfg.cooldown,
    })
    print(f"  mode: {mode}" + (" (systemd user session NOT required)" if mode == "systemd" else ""))
    print(f"  kill-switch: {'on' if kill_switch else 'OFF — ' + why}")

    if mode == "systemd":
        mkdirp(UNIT_DIR)
        with open(UNIT_PATH, "w") as fh:
            fh.write(UNIT_TEMPLATE.format(bin=BIN_PATH, state_dir=cfg.state_dir,
                                          home=cfg.target_home))
        systemctl("daemon-reload")
        rc, out = systemctl("enable", "--now", UNIT_NAME)
        if rc != 0:
            print(f"  systemctl enable failed: {out}; falling back to portable",
                  file=sys.stderr)
            mode = "portable"
            merged = read_json(cfg.config_file, {}) or {}
            merged.update({"mode": "portable", "target_home": cfg.target_home,
                           "kill_switch": kill_switch, "version": VERSION})
            atomic_write_json(cfg.config_file, merged)
    if mode == "portable":
        # stop any previous instance first (idempotent install)
        cmd_stop(args, cfg, log, quiet=True)
        mkdirp(cfg.state_dir)
        rc = daemonize(cfg, ["supervise"])
        if rc != 0:
            return rc
    # make sure the tunnel is actually up, not just configured
    warp = Warp(cfg, log)
    if cfg.warp_bin and not warp.connected():
        print("  bringing the WARP tunnel up...")
        warp.mode_warp()
        warp.connect()
        for _ in range(5):
            time.sleep(2)
            if warp.connected():
                break
        if not warp.connected():
            print("  no usable WARP registration — registering a new device")
            for attempt in range(1, 4):
                ok, out = warp.registration_new()
                if ok:
                    break
                time.sleep(3 * attempt)
            warp.mode_warp()
            warp.connect()
            for _ in range(10):
                time.sleep(2)
                if warp.connected():
                    break
    if cfg.warp_bin:
        print(f"  tunnel: {'connected' if warp.connected() else 'NOT connected'}"
              f"  egress: {probe_egress(cfg) or 'none'}")
    pinner = Pinner(cfg, log, State(cfg))
    if cfg.pin and warp.connected():
        if pinner.install():
            print(f"  ipv6 pin: on for {', '.join(cfg.pin_hosts)}")
    print("  installed.")
    return 0


def cmd_start(args, cfg: Config, log: Log) -> int:
    if cfg.mode == "systemd" and systemd_available() and not args.portable:
        rc, out = systemctl("start", UNIT_NAME)
        print(out or "started")
        return rc
    alive, pid, why = supervisor_running(cfg)
    if alive:
        print(f"already running (pid {pid})")
        return 0
    return daemonize(cfg, ["supervise"])


def cmd_stop(args, cfg: Config, log: Log, quiet: bool = False) -> int:
    if cfg.mode == "systemd" and systemd_available() and not args.portable:
        rc, out = systemctl("stop", UNIT_NAME)
        if not quiet:
            print(out or "stopped")
        # belt and braces: never leave the guard installed, never leave a pin
        Firewall(cfg, log).teardown()
        Pinner(cfg, log).remove()
        return 0
    pid = read_pidfile(cfg.pid_file)
    if not pid or not pid_alive(pid):
        Firewall(cfg, log).teardown()
        Pinner(cfg, log).remove()
        if not quiet:
            print("not running")
        return 0
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    for _ in range(60):
        if not pid_alive(pid):
            break
        time.sleep(0.25)
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    Firewall(cfg, log).teardown()
    try:
        os.unlink(cfg.pid_file)
    except OSError:
        pass
    if not quiet:
        print("stopped")
    return 0


def cmd_pin(args, cfg: Config, log: Log) -> int:
    pinner = Pinner(cfg, log, State(cfg))
    action = getattr(args, "action", "status") or "status"
    if action == "on":
        if not pinner.install():
            print("could not pin (no IPv6 address resolved, or pinning disabled)",
                  file=sys.stderr)
            return 1
        print(f"pinned {', '.join(cfg.pin_hosts)} to IPv6 in {cfg.hosts_file}")
        return 0
    if action == "off":
        pinner.remove()
        print("pin removed")
        return 0
    st = pinner.status()
    print(json.dumps(st, indent=2))
    return 0 if (st["installed"] or not st["enabled"]) else 1


def cmd_verify(args, cfg: Config, log: Log) -> int:
    """One real request: is the free tier usable from here right now?"""
    v = verify_request(cfg, log)
    print(json.dumps({**v, "model": cfg.test_model, "ipv6_egress": ipv6_egress(cfg)}, indent=2))
    if not v.get("ran"):
        return 2
    return 0 if v.get("ok") else 1


def cmd_status(args, cfg: Config, log: Log) -> int:
    state = State(cfg).load()
    alive, pid, why = supervisor_alive(cfg)
    warp = Warp(cfg, log)
    hb = read_json(cfg.heartbeat, {}) or {}
    ks_installed, ks_verified = guard_state(cfg, state, log)
    pinner = Pinner(cfg, log, State(cfg))
    pin_block = pinner.current_block()
    # `current` must follow reality: a reconnect can change the address with no
    # rotation at all, and `status` is the one place a user looks at both.
    egress_live = probe_egress(cfg)
    try:
        sync_current(cfg, State(cfg), log,
                     egress=egress_live, why="ouc status")
        state = State(cfg).load()
    except Exception:
        pass
    info = {
        "version": VERSION,
        "mode": cfg.mode,
        "supervisor": {"alive": alive, "pid": pid, "reason": why,
                       "heartbeat_age_s": now() - int(hb.get("ts") or 0) if hb else None},
        "warp": {"connected": warp.connected(), "status": warp.status()},
        "egress": egress_live,
        "current_block": norm64(state.get("current") or "") or norm64(probe_egress(cfg)),
        "kill_switch": {"configured": cfg.kill_switch, "reason": cfg.kill_switch_reason,
                        "installed": ks_installed, "verified": ks_verified,
                        "degraded": state.get("degraded")},
        "rotation": {"hops": state.get("hops", 0), "last_hop": state.get("last_hop", 0),
                     "blacklisted_blocks": len([b for b in state.get("blacklist", [])
                                                if norm64(b)]),
                     "last_block_reused": bool(state.get("last_block_reused"))},
        "warp_mode": cfg.warp_mode,
        "proxy": {"enabled": cfg.proxy_mode,
                  "socks": f"127.0.0.1:{cfg.proxy_socks_port}",
                  "http": cfg.http_proxy,
                  "socks_open": port_open("127.0.0.1", cfg.proxy_socks_port),
                  "bridge_open": port_open("127.0.0.1", cfg.proxy_http_port)},
        "pin": {"enabled": cfg.pin, "installed": bool(pin_block),
                "hosts": cfg.pin_hosts, "file": cfg.hosts_file,
                "addresses": [l.split()[0] for l in pin_block.splitlines() if l.strip()]},
        "ipv6_egress": ipv6_egress(cfg),
        "last_rotation": state.get("last_rotation") or {},
        "pending_wall": state.get("pending_wall", False),
        "last_error": state.get("last_error", ""),
        "path": {"state": cfg.state_file, "log": cfg.opencode_log,
                 "supervisor_log": cfg.supervisor_log},
    }
    if args.json:
        print(json.dumps(info, indent=2))
        return 0
    ok = lambda b: "✓" if b else "✗"
    print(f"\n  {BRAND} {VERSION}  (mode: {cfg.mode})\n")
    print(f"  {ok(alive)} supervisor: {'running (pid %s)' % pid if alive else 'NOT running — ' + why}")
    print(f"  {ok(info['warp']['connected'])} warp tunnel: "
          f"{info['warp']['status'].splitlines()[0] if info['warp']['status'] else 'no warp-cli'}")
    print(f"  {ok(bool(norm64(info['egress'])))} egress: {info['egress'] or 'unreachable'}"
          f"   block: {info['current_block'] or '?'}")
    ks = info["kill_switch"]
    if not ks["configured"]:
        ks_txt = "off (" + cfg.kill_switch_reason + ")"
    elif ks["installed"]:
        ks_txt = "installed" + ("" if ks["verified"]
                                else " (as recorded by the supervisor — run with sudo to verify)")
    elif ks["verified"]:
        ks_txt = "NOT installed"
    else:
        ks_txt = "unknown (run with sudo to verify)"
    print(f"  {'!' if ks['degraded'] else ok(ks['installed'])} kill-switch: {ks_txt}"
          + ("   ** DEGRADED: real IP visible while repairing **" if ks["degraded"] else ""))
    print(f"  ✓ rotations: {info['rotation']['hops']}   "
          f"blacklisted blocks: {info['rotation']['blacklisted_blocks']}")

    print(f"  ✓ resumes: {int(state.get('resumes') or 0)}   "
          f"walls seen: {int(state.get('walls_seen') or 0)}")
    px = info["proxy"]
    if px["enabled"]:
        print(f"  {'✓' if px['socks_open'] else '!'} warp mode: proxy (no TUN) "
              f"— SOCKS5 {px['socks']}")
        print(f"  {'✓' if px['bridge_open'] else '!'} http bridge: {px['http']} "
              + ("(opencode uses it)" if px["bridge_open"] else "NOT running — run: sudo ouc start"))
    else:
        print(f"  ✓ warp mode: full tunnel (TUN {cfg.tun_iface}), kill-switch armed")

    pin = info["pin"]
    if pin["enabled"]:
        print(f"  {'✓' if pin['installed'] else '!'} ipv6 pin: "
              + (f"on ({', '.join(pin['addresses']) or 'resolved'}) — opencode uses the tunnel"
                 if pin["installed"] else "off — opencode may use IPv4, where rotating /64s does nothing"))
    else:
        print("  ! ipv6 pin: disabled (OUC_PIN=0)")
    if state.get("last_block_reused"):
        print("  ! the last rotation landed on a /64 whose free quota was already "
              "spent — the tunnel works, but you may stay limited until the "
              "window resets")
    if state.get("pending_wall"):
        print("  ! a quota wall was detected and is waiting for the cooldown to expire")
    if state.get("last_error"):
        print(f"  ! last error: {state['last_error']}")
    print()
    return 0 if alive else 1


def cmd_supervise(args, cfg: Config, log: Log) -> int:
    root = os.geteuid() == 0
    if not root:
        print("supervise needs root (it manages the tunnel and the firewall)",
              file=sys.stderr)
        return 1
    if args.foreground or cfg.mode == "systemd":
        Supervisor(cfg, Log(cfg.supervisor_log, echo=True)).run_forever()
        return 0
    return daemonize(cfg, ["supervise"])


def cmd_hop(args, cfg: Config, log: Log) -> int:
    """Manual rotation. As root: do it now. As a user: ask the supervisor."""
    if os.geteuid() == 0:
        res = rotate(cfg, log, State(cfg), why="manual-hop")
        _print_hop(res)
        return 0 if res["ok"] else 1

    # unprivileged path: drop a request file the root supervisor polls
    alive, pid, why = supervisor_alive(cfg)
    if not alive:
        print("supervisor is not running — start it with: sudo ouc start",
              file=sys.stderr)
        return 1
    rid = uuid.uuid4().hex[:12]
    payload = {"id": rid, "ts": now(), "user": os.environ.get("USER", "")}
    channels = []
    try:
        mkdirp(cfg.user_state_dir)
        atomic_write_json(cfg.hop_request, payload, mode=0o644)
        channels.append((cfg.hop_request, cfg.hop_result))
    except OSError:
        pass
    if not channels:
        try:
            os.makedirs(cfg.hop_fallback_dir, mode=0o1777, exist_ok=True)
            try:
                os.chmod(cfg.hop_fallback_dir, 0o1777)
            except OSError:
                pass
            atomic_write_json(cfg.hop_fallback_request, payload, mode=0o666)
            try:
                os.chmod(cfg.hop_fallback_request, 0o666)
            except OSError:
                pass
            channels.append((cfg.hop_fallback_request, cfg.hop_fallback_result))
        except OSError as exc:
            print(f"cannot hand a hop request to the supervisor: {exc}", file=sys.stderr)
            return 1
    t0 = time.time()
    wait = args.wait if args.wait else 90
    while time.time() - t0 < wait:
        time.sleep(1)
        res = None
        for _, rp in channels:
            r = read_json(rp, None)
            if isinstance(r, dict) and r.get("id") == rid:
                res = r
                break
        if isinstance(res, dict) and res.get("id") == rid:
            _print_hop(res)
            return 0 if res.get("ok") else 1
    print("no answer from the supervisor (timed out)", file=sys.stderr)
    return 1


def _print_hop(res: dict) -> None:
    res = res or {"ok": False, "error": "rotation did not run"}
    old64 = norm64(res.get("old", "")) or "?"
    new64 = norm64(res.get("new", "")) or "?"
    print(f"  old block: {old64}")
    print(f"  new block: {new64}")
    if res.get("ok"):
        print(f"  ✓ rotated in {res.get('duration', '?')}s — resend your message")
    else:
        print(f"  ✗ rotation failed: {res.get('error', 'unknown')}")
        print("    the machine keeps its internet; try again, or run: ouc repair")


def cmd_repair(args, cfg: Config, log: Log) -> int:
    res = rotate(cfg, log, State(cfg), why="tunnel-down")
    _print_hop(res)
    return 0 if res["ok"] else 1


def cmd_rescue(args, cfg: Config, log: Log) -> int:
    """Get the machine back to a working network, whatever state we're in."""
    print("removing the leak guard...")
    Firewall(cfg, log).teardown()
    warp = Warp(cfg, log)
    if not warp.connected():
        print("reconnecting the tunnel...")
        warp.mode_warp()
        warp.connect()
    print(f"  tunnel: {warp.status().splitlines()[0] if warp.status() else 'unknown'}")
    print(f"  egress: {probe_egress(cfg) or 'unreachable'}")
    print("done. If the egress is empty, the tunnel is still down but the firewall "
          "is gone, so normal internet works.")
    return 0


def cmd_killswitch(args, cfg: Config, log: Log) -> int:
    fw = Firewall(cfg, log)
    if args.action == "on":
        ok = fw.install()
        print("installed" if ok else "could not install (see ouc doctor)")
        return 0 if ok else 1
    if args.action == "off":
        fw.teardown()
        print("removed")
        return 0
    print("installed" if fw.installed() else "not installed",
          f"({cfg.kill_switch_reason})")
    return 0


def doctor_proxy(cfg: Config, out: list[str]) -> None:
    """The extra checks that matter when there is no TUN device."""
    out.append("")
    out.append("proxy mode (no TUN):")
    out.append(f"  warp mode            : {cfg.warp_mode}")
    out.append(f"  SOCKS5 127.0.0.1:{cfg.proxy_socks_port:<5} : "
               f"{'open' if port_open('127.0.0.1', cfg.proxy_socks_port) else 'CLOSED'}")
    out.append(f"  HTTP bridge {cfg.http_proxy} : "
               f"{'open' if port_open('127.0.0.1', cfg.proxy_http_port) else 'CLOSED'}")
    W = Warp(cfg, log)
    out.append(f"  warp-cli status      : {(W.status().splitlines() or ['(no answer)'])[0]}")
    egress = probe_egress(cfg)
    out.append(f"  egress via the proxy : {egress or '(none — proxy broken?)'}")
    fam = "IPv6 /64" if ":" in egress else ("IPv4 (portable, not rotatable)" if egress else "unknown")
    out.append(f"  egress family        : {fam}")
    if egress and ":" in egress:
        out.append(f"  current block        : {norm64(egress)}")
    out.append(f"  opencode env         : opencode must run with "
               f"HTTPS_PROXY={cfg.http_proxy}")


def cmd_doctor(args, cfg: Config, log: Log) -> int:
    state = State(cfg).load()
    warp = Warp(cfg, log)
    fw = Firewall(cfg, log)
    alive, pid, why = supervisor_alive(cfg)
    problems = []
    ok = lambda c: "✓" if c else "✗"
    print(f"\n  {BRAND} doctor — v{VERSION}\n")
    tun_ok = warp.connected()
    label = "warp tunnel (proxy mode)" if cfg.proxy_mode else "warp tunnel"
    print(f"  {ok(tun_ok)} {label}")
    if not tun_ok:
        if cfg.proxy_mode:
            problems.append(
                f"proxy mode is not usable: warp says "
                f"'{(warp.status().splitlines() or ['?'])[0]}' and nothing is "
                f"listening on 127.0.0.1:{cfg.proxy_socks_port}")
        else:
            problems.append("tunnel down: run 'sudo ouc repair'")
    print(f"  {ok(alive)} supervisor ({cfg.mode})")
    if not alive:
        problems.append("supervisor not running: 'sudo ouc start'")
    egress = probe_egress(cfg)
    print(f"  {ok(bool(norm64(egress)))} IPv6 egress: {egress or 'none'}")
    if not norm64(egress):
        problems.append("no IPv6 egress")
    avail, why2 = fw.available()
    ks_inst, ks_ver = guard_state(cfg, State(cfg), log)
    ks_mark = ok(ks_inst) if cfg.kill_switch else "–"   # "off on purpose" is not a failure
    print(f"  {ks_mark} kill-switch"
          + ("" if cfg.kill_switch else f" (off: {cfg.kill_switch_reason})")
          + ("" if ks_ver else " (as recorded; sudo verifies)"))
    if cfg.kill_switch and avail and ks_ver and not ks_inst:
        problems.append("kill-switch configured but not installed")
    print(f"  {'!' if state.get('degraded') else '✓'} leak posture: "
          + ("DEGRADED — real IP visible while the tunnel is being repaired"
             if state.get("degraded") else "tunnel-only"))
    print(f"  {ok(os.path.exists(cfg.opencode_log))} opencode log readable: {cfg.opencode_log}")
    print(f"  {ok(bool(cfg.oc_bin and os.access(cfg.oc_bin, os.X_OK)))} opencode binary: {cfg.oc_bin}")
    print(f"  ✓ rotations: {state.get('hops', 0)}  "
          f"blocks blacklisted: {len([b for b in state.get('blacklist', []) if norm64(b)])}")

    print(f"  {ok(bool(cfg.oc_bin and os.access(cfg.oc_bin, os.X_OK)))} "
          f"auto-resume: {'on (supervisor may continue the session)' if cfg.auto_resume else 'off (ouc run always resumes)'}"
          f"   max {cfg.resume_max} attempt(s), budget {cfg.resume_budget}s")
    bad = [b for b in state.get("blacklist", []) if not norm64(b)]
    print(f"  {ok(not bad)} blacklist hygiene (no empty/invalid blocks)")
    # advertising check: this build must never plant agent rules
    planted = os.path.join(cfg.target_home, ".opencode/AGENTS.md")
    if os.path.exists(planted):
        print(f"  ! {planted} exists — it is not ours; this build never writes agent rules")
    else:
        print("  ✓ no agent-rule/credits file planted by this tool")
    if cfg.proxy_mode:
        print()
        print("  proxy mode (no TUN):")
        print(f"    warp mode            : {cfg.warp_mode}")
        print(f"    SOCKS5 127.0.0.1:{cfg.proxy_socks_port:<5} : "
              f"{'open' if port_open('127.0.0.1', cfg.proxy_socks_port) else 'CLOSED'}")
        print(f"    HTTP bridge {cfg.http_proxy} : "
              f"{'open' if port_open('127.0.0.1', cfg.proxy_http_port) else 'CLOSED'}")
        egress = probe_egress(cfg)
        fam = ("IPv6 /64 (rotatable)" if egress and ":" in egress
               else ("IPv4 — the proxy is bypassed" if egress else "unknown"))
        print(f"    egress via the proxy : {egress or '(none — proxy broken?)'}")
        print(f"    egress family        : {fam}")
        if egress and ":" in egress:
            print(f"    current block        : {norm64(egress)}")
        print(f"    opencode must run with HTTPS_PROXY={cfg.http_proxy}")
        print(f"    or simply            : ouc lane <your opencode args>")
        if not port_open("127.0.0.1", cfg.proxy_http_port):
            problems.append("HTTP bridge is down: run 'sudo ouc start' or 'ouc hop'")
        if egress and ":" not in egress:
            problems.append("the proxy is not being used: check HTTPS_PROXY "
                            "in the environment opencode runs in")
    print()
    for p in problems:
        print(f"  ! {p}")
    return 0 if not problems else 1


def cmd_lane(args, cfg: Config, log: Log) -> int:
    """Launch the real opencode with opencode.ai pinned to an IPv6 edge.

    Guarantees:
      * stdout is the program's own stdout — we exec, we never pipe.
      * nothing is appended, prefixed or signed.
    """
    real = cfg.oc_bin
    if not real or not os.access(real, os.X_OK):
        print(f"opencode binary not found ({real}); set OUC_OC_BIN", file=sys.stderr)
        return 127

    if cfg.proxy_mode:
        # proxy mode: the exit is a local HTTP proxy, so hand it to opencode
        # and exec. No hosts file, no namespaces, no capabilities involved.
        os.environ["HTTP_PROXY"] = os.environ["HTTPS_PROXY"] = cfg.http_proxy
        os.environ["http_proxy"] = os.environ["https_proxy"] = cfg.http_proxy
        os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1,::1")
        os.environ.setdefault("no_proxy", os.environ["NO_PROXY"])
        Bridge(cfg, Log(os.path.join(cfg.state_dir, "ouc.log"))).ensure()
        os.execv(real, [real, *args])
        return 0

    addrs: list[str] = []
    try:
        for info in socket.getaddrinfo("opencode.ai", 443, socket.AF_INET6):
            a = info[4][0]
            if a not in addrs and "%" not in a:
                addrs.append(a)
    except OSError:
        addrs = []
    addrs = addrs[:2]

    if addrs and shutil.which("unshare"):
        # root already has CAP_SYS_ADMIN: a plain mount namespace keeps its
        # identity. Non-root needs a user namespace (-r). Probing first avoids
        # the case where unshare succeeds but the bind mount does not.
        nsflags = ["-m"] if os.geteuid() == 0 else ["-rm"]
        try:
            usable = subprocess.run(["unshare", *nsflags, "true"],
                                    capture_output=True, timeout=10).returncode == 0
        except Exception:
            usable = False
        if usable:
            try:
                td = tempfile.mkdtemp(prefix="ouc-lane.")
                hosts = os.path.join(td, "hosts")
                with open(hosts, "w") as fh:
                    for name in ("opencode.ai", "www.opencode.ai", "api.opencode.ai"):
                        for a in addrs:
                            fh.write(f"{a} {name}\n")
                os.chmod(td, 0o755)
                os.chmod(hosts, 0o644)
                cmd = ["unshare", *nsflags, "--", "bash", "-c",
                       'mount --bind "$1" /etc/hosts || exit 97; shift; exec "$@"',
                       "_", hosts, real, *args]
                os.execvp(cmd[0], cmd)
            except Exception as exc:  # fall through to the plain exec below
                print(f"[ouc] lane: IPv6 pin unavailable ({exc}); running directly",
                      file=sys.stderr)
        else:
            print("[ouc] lane: mount namespaces unavailable; running directly",
                  file=sys.stderr)
    else:
        print("[ouc] lane: no IPv6 address for opencode.ai or unshare missing; "
              "running directly (traffic still uses the tunnel)", file=sys.stderr)
    os.execv(real, [real, *args])
    return 0


def cmd_scan_once(args, cfg: Config, log: Log) -> int:
    st = State(cfg)
    res = scan_wall(cfg, st, Log(cfg.supervisor_log))
    rot = None
    # consult pending state even when this pass saw no *new* wall bytes:
    # a wall detected during a cooldown must still be served afterwards.
    if not args.no_rotate:
        rot = maybe_rotate_for_wall(cfg, st, log)
    print(json.dumps({"scan": res, "rotation": rot}, indent=2))
    return 0


def cmd_config(args, cfg: Config, log: Log) -> int:
    print(json.dumps(cfg.as_dict(), indent=2))
    return 0


# ---------------------------------------------------------------- uninstall

def cmd_uninstall(args, cfg: Config, log: Log) -> int:
    root = os.geteuid() == 0
    if not root and not args.assume_root:
        print("uninstall needs root", file=sys.stderr)
        return 1
    print("  stopping supervisor...")
    ns = argparse.Namespace(portable=False)
    cfg.mode = "portable" if not systemd_available() else cfg.mode
    cmd_stop(ns, cfg, log, quiet=True)

    print("  removing systemd units...")
    if systemd_available():
        for unit in (UNIT_NAME, f"{BRAND}-keeper.service", f"{BRAND}-guard.service"):
            systemctl("disable", "--now", unit)
        systemctl("daemon-reload")
    for path in (UNIT_PATH,
                 os.path.expanduser(f"~/.config/systemd/user/{BRAND}-guard.service"),
                 os.path.join(cfg.target_home, f".config/systemd/user/{BRAND}-guard.service"),
                 f"/usr/local/bin/{BRAND}-keeper"):
        try:
            os.unlink(path)
            print(f"    removed {path}")
        except OSError:
            pass

    print("  removing firewall...")
    Firewall(cfg, log).teardown()

    print("  removing the ipv6 pin...")
    if Pinner(cfg, log).remove():
        print(f"    cleaned {cfg.hosts_file}")

    print("  removing legacy sudoers entry...")
    for path in (f"/etc/sudoers.d/{BRAND}",):
        if os.path.exists(path):
            try:
                os.unlink(path)
                print(f"    removed {path}")
            except OSError as exc:
                print(f"    could not remove {path}: {exc}", file=sys.stderr)

    print("  removing legacy advertising file...")
    marker = base64.b64decode(LEGACY_AD_MARKER_B64).decode()
    ad = os.path.join(cfg.target_home, ".opencode/AGENTS.md")
    try:
        if os.path.exists(ad):
            body = open(ad, "r", errors="replace").read()
            if marker in body:
                os.unlink(ad)
                print(f"    removed legacy ad file {ad}")
            else:
                print(f"    kept {ad} (not ours)")
    except OSError:
        pass

    print("  restoring the opencode entry point...")
    wrapper = os.path.join(cfg.target_home, ".local/bin/opencode")
    real = os.path.join(cfg.target_home, ".opencode/bin/opencode")
    try:
        if os.path.exists(wrapper):
            body = open(wrapper, "r", errors="replace").read()
            if "zen-lane" in body or "ouc" in body:
                if os.path.exists(real):
                    with open(wrapper, "w") as fh:
                        fh.write(f'#!/usr/bin/env bash\nexec "{real}" "$@"\n')
                    os.chmod(wrapper, 0o755)
                    print("    wrapper reset to the stock binary")
                else:
                    os.unlink(wrapper)
                    print("    wrapper removed")
    except OSError:
        pass

    print("  removing legacy + current files...")
    for path in (
        os.path.join(cfg.target_home, f".local/share/{BRAND}"),   # legacy dir
        LIB_DIR,
        BIN_PATH,
        os.path.join(cfg.target_home, ".local/bin/opencode-warp"),
        os.path.join(cfg.target_home, f".local/bin/{BRAND}-hop"),
        os.path.join(cfg.target_home, f".local/bin/{BRAND}-doctor"),
        os.path.join(cfg.target_home, f".local/bin/{BRAND}-keeper"),
    ):
        try:
            if os.path.islink(path) or os.path.isfile(path):
                os.unlink(path)
                print(f"    removed {path}")
            elif os.path.isdir(path):
                shutil.rmtree(path)
                print(f"    removed {path}")
        except OSError:
            pass
    for path in (os.path.join(cfg.target_home, ".local/state", BRAND),
                 cfg.hop_fallback_dir):
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
                print(f"    removed {path}")
        except OSError:
            pass
    if not args.keep_state:
        try:
            shutil.rmtree(cfg.state_dir)
            print(f"    removed {cfg.state_dir}")
        except OSError:
            pass
    print("  uninstalled. opencode itself and your sessions are untouched.")
    print("  (the cloudflare-warp package is left installed on purpose)")
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# auto-resume: quota wall -> wait for a working block -> continue the session
#
# The behaviour this implements, in the user's words: "when it reaches the free
# limit, wait, check that the next WARP station actually works, then tell the
# model to continue where it left off."
#
# Three deliberate design rules:
#   * never resume on an unverified block — a rotation alone proves nothing;
#   * never resume twice for the same wall (state marker + lock);
#   * never loop forever — bounded attempts, bounded budget, honest failure.
# --------------------------------------------------------------------------


class LogFollower:
    """Incremental reader for opencode's log, independent of the supervisor.

    It keeps its own byte offset and inode in state.json, so `ouc run` and the
    background supervisor can both watch the same file without stealing bytes
    from each other and without re-triggering on history.
    """

    def __init__(self, cfg: Config, state: "State",
                 keys: tuple = ("run_offset", "run_log_id")):
        self.cfg = cfg
        self.state = state
        self.off_key, self.id_key = keys

    def poll(self) -> bytes:
        path = self.cfg.opencode_log
        try:
            st = os.stat(path)
        except OSError:
            return b""
        with StateLock(self.cfg.state_lock):
            s = self.state.load()
            ident = f"{st.st_dev}:{st.st_ino}"
            offset = int(s.get(self.off_key) or 0)
            if s.get(self.id_key) != ident or st.st_size < offset:
                offset = 0                      # new or truncated log
            if st.st_size == offset:
                return b""
            try:
                with open(path, "rb") as fh:
                    fh.seek(offset)
                    chunk = fh.read(min(MAX_LOG_READ, st.st_size - offset))
            except OSError:
                return b""
            s.update({self.id_key: ident, self.off_key: offset + len(chunk)})
            self.state.save(s)
        return chunk

    def skip_history(self) -> None:
        """Advance to the end without inspecting anything already written."""
        self.poll()


def wall_from_chunk(chunk: bytes) -> "dict | None":
    """Return {session, model, hit} for the last quota wall in `chunk`."""
    if not chunk:
        return None
    text = chunk.decode("utf-8", "replace")
    hit = next((p.decode() for p in WALL_PATTERNS if p.decode() in text), "")
    if not hit:
        return None
    session, model = "", ""
    for line in text.splitlines():
        m = SESSION_RE.search(line)
        if m:
            session = m.group(1)
        m2 = MODEL_RE.search(line)
        if m2:
            model = f"{m2.group(1)}/{m2.group(2)}"
    return {"session": session, "model": model, "hit": hit}


def opencode_env(cfg: Config) -> dict:
    """The environment opencode must inherit to be inside the tunnel."""
    env = dict(os.environ)
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    if cfg.proxy_mode:
        env["HTTP_PROXY"] = env["HTTPS_PROXY"] = cfg.http_proxy
        env["http_proxy"] = env["https_proxy"] = cfg.http_proxy
        env.setdefault("NO_PROXY", "localhost,127.0.0.1,::1")
        env.setdefault("no_proxy", env["NO_PROXY"])
    return env


def quota_reset_hint(cfg: Config, timeout: int = 8) -> "int | None":
    """Seconds until the free-tier window reopens, from the API's Retry-After.

    Best effort, and deliberately unauthenticated: the same call a bare client
    makes gets the same 429 the wall comes from, and its Retry-After points at
    the end of the window (measured 2026-11-09: it counts down to 00:00 UTC).
    Used only for telling the user the truth about waiting — never as proof.
    """
    url = os.environ.get("OUC_QUOTA_HINT_URL",
                         "https://opencode.ai/inference/openai/v1/chat/completions")
    payload = json.dumps({"model": (cfg.resume_model or "deepseek-v4.1-flash-free")
                                        .split("/")[-1],
                          "messages": [{"role": "user", "content": "hi"}]}).encode()
    try:
        p = subprocess.run(
            ["curl", "-sS", "-D", "-", "-o", "/dev/null", "-m", str(timeout),
             "-X", "POST", url, "-H", "content-type: application/json",
             "--data-binary", "@-"],
            input=payload, capture_output=True, timeout=timeout + 5)
        head = p.stdout.decode("utf-8", "replace")
    except Exception:
        return None
    m = re.search(r"(?im)^retry-after:\s*(\d+)", head)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def resume_argv(cfg: Config, session: str = "", model: str = "",
                prompt: str = "") -> list:
    """The exact command that continues an interrupted session.

    `-c` (continue the last session in this directory) is the default because
    it is the one that survives real life: cleaned state, a different cwd, or a
    session id that belongs to opencode's title agent. `-s <id>` is opt-in via
    OUC_RESUME_USE_SESSION_ID=1, and cmd_run falls back to `-c` if it fails.
    """
    argv = [cfg.oc_bin or "opencode", "run"]
    use_session = bool(session) and bool(getattr(cfg, "resume_use_session", False))
    argv += (["-s", session] if use_session else ["-c"])
    m = model or cfg.resume_model
    if m:
        argv += ["-m", m]
    argv += [prompt or cfg.resume_prompt]
    return argv


def _sleep_quietly(log: "Log", seconds: int, why: str) -> None:
    end = time.time() + max(0, seconds)
    while time.time() < end:
        left = int(end - time.time())
        log(f"{why}: waiting {left}s")
        time.sleep(min(15, max(1, left)))


def rotate_until_healthy(cfg: Config, log: "Log", state: "State",
                         deadline: float, why: str = "auto-resume") -> dict:
    """Rotate until a real request succeeds, or the deadline passes.

    This *is* the "wait and check the next WARP station" step: the rotate()
    machinery already verifies each candidate with a real request, so a
    returned ok=True means a block that answered — not merely a new address.
    """
    out = {"ok": False, "block": "", "attempts": 0, "detail": ""
           , "verified": False}
    while time.time() < deadline:
        out["attempts"] += 1
        rot = rotate(cfg, log, state, why=why)
        ok = bool(rot.get("ok"))
        verified = bool(rot.get("verified"))
        if ok and (verified or not cfg.verify):
            out.update(ok=True, verified=verified,
                       block=norm64(rot.get("new")) or ipv6_egress(cfg),
                       detail="verified with a real request" if verified
                              else "accepted without verification")
            log(f"{why}: healthy block {out['block']} "
                f"(attempt {out['attempts']})")
            return out
        out["detail"] = (rot.get("error") or rot.get("how")
                         or "no candidate could be confirmed")
        log(f"{why}: attempt {out['attempts']} did not produce a working "
            f"block ({out['detail']})", "WARN")
        remaining = deadline - time.time()
        if remaining <= 1:
            break
        _sleep_quietly(log, min(cfg.resume_backoff or 3, int(remaining)),
                       f"{why}: retrying")
    return out


def cmd_run(args, cfg: Config, log: Log) -> int:
    """Run opencode; when the free limit hits, wait for a good block and go on.

    This is the recommended way to use the tool interactively: one command,
    no wrapper scripts, and the session keeps its context because the resume
    uses opencode's own --session/--continue, not a fresh conversation.
    """
    extra = list(getattr(args, "args", []) or [])
    if extra and extra[0] == "--":
        extra = extra[1:]
    if not (cfg.oc_bin and os.access(cfg.oc_bin, os.X_OK)):
        print("  ✗ opencode binary not found (set OUC_OC_BIN)", file=sys.stderr)
        return 2

    state = State(cfg)
    follower = LogFollower(cfg, state)
    follower.skip_history()          # only walls we cause may trigger a resume

    base = [cfg.oc_bin, "run"] + extra
    env = opencode_env(cfg)
    started = time.time()
    resumes = 0
    wall_model = ""

    print(f"  ouc run — opencode under supervision "
          f"(auto-resume x{cfg.resume_max})", flush=True)
    resumed_with_session = False
    fallback_tried = False
    while True:
        log(f"run: starting opencode (attempt {resumes + 1}, "
            f"{' '.join(extra) or 'no args'})")
        try:
            rc = subprocess.call(base, env=env)
        except KeyboardInterrupt:
            print("", file=sys.stderr)
            return 130
        except OSError as exc:
            print(f"  ✗ could not start opencode: {exc}", file=sys.stderr)
            return 2

        wall = wall_from_chunk(follower.poll())
        if rc == 0 and not wall:
            if resumes:
                with StateLock(cfg.state_lock):
                    s = state.load()
                    state.save({**s, "resumes": int(s.get("resumes") or 0) + resumes})
            log(f"run: finished (exit 0) after {resumes} resume(s)")
            return 0
        if not wall:
            if rc != 0 and resumed_with_session and not fallback_tried:
                # "-s <id>" is fragile: if opencode does not know that session
                # it exits non-zero without ever touching the quota. That is
                # not a wall, so retry once with "continue the last session".
                fallback_tried = True
                log("run: the session id was not usable — retrying with -c", "WARN")
                print("  ! that session id was not usable — continuing the most "
                      "recent session instead", file=sys.stderr)
                base = resume_argv(cfg, session="", model=wall_model)
                env = opencode_env(cfg)
                continue
            if rc == 0:
                log(f"run: finished (exit 0) after {resumes} resume(s)")
            else:
                log(f"run: opencode exited {rc} with no quota wall", "WARN")
            return rc

        with StateLock(cfg.state_lock):
            s = state.load()
            state.save({**s,
                        "walls_seen": int(s.get("walls_seen") or 0) + 1,
                        "last_wall": now(), "pending_wall": False})
        wall_model = wall.get("model", "")
        log(f"run: quota wall hit (session {wall['session'] or 'unknown'}, "
            f"model {wall['model'] or 'unknown'})", "WARN")

        if resumes >= cfg.resume_max:
            print(f"  ✗ the free limit came back after {resumes} resume(s); "
                  f"stopping (OUC_RESUME_MAX={cfg.resume_max})", file=sys.stderr)
            return 1
        elapsed = time.time() - started
        if cfg.resume_budget and elapsed >= cfg.resume_budget:
            print(f"  ✗ resume budget of {cfg.resume_budget}s spent; stopping",
                  file=sys.stderr)
            return 1

        deadline = time.time() + max(30, min(cfg.resume_budget or 300, 300))
        print("  … free limit reached: waiting for a WARP block that actually "
              "answers", flush=True)
        health = rotate_until_healthy(cfg, log, state, deadline=deadline)

        if not health["ok"]:
            hint = quota_reset_hint(cfg)
            if hint:
                print(f"  ! no working block right now; the free window reopens "
                      f"in about {hint // 60} min", file=sys.stderr)
            if hint and cfg.resume_wait_reset:
                wait = hint if not cfg.resume_max_wait else min(hint, cfg.resume_max_wait)
                print(f"  … waiting {wait // 60} min for the window, as "
                      f"OUC_RESUME_WAIT_RESET=1 asks", flush=True)
                _sleep_quietly(log, wait, "run: waiting for the quota window")
                health = rotate_until_healthy(cfg, log, state,
                                             deadline=time.time() + 300,
                                             why="after-window-wait")
            if not health["ok"]:
                print("  ✗ could not find a working block; nothing was sent. "
                      "Re-run `ouc run` later or `ouc hop` by hand.",
                      file=sys.stderr)
                return 1

        resumes += 1
        resumed_with_session = bool(wall.get("session")) and cfg.resume_use_session
        base = resume_argv(cfg, session=wall.get("session", ""),
                           model=wall.get("model", ""))
        env = opencode_env(cfg)
        print(f"  ↻ continuing the same session on {health['block']} "
              f"(resume {resumes}/{cfg.resume_max})", flush=True)


def cmd_resume(args, cfg: Config, log: Log) -> int:
    """Continue the last opencode session once the quota really works."""
    if not (cfg.oc_bin and os.access(cfg.oc_bin, os.X_OK)):
        print("  ✗ opencode binary not found (set OUC_OC_BIN)", file=sys.stderr)
        return 2
    state = State(cfg)
    session = getattr(args, "session", "") or ""
    model = getattr(args, "model", "") or cfg.resume_model or ""
    prompt = getattr(args, "prompt", "") or cfg.resume_prompt

    v = verify_request(cfg, log)
    if not v.get("ok"):
        log(f"resume: current block is not usable ({v.get('detail')}) — "
            f"looking for another one", "WARN")
        health = rotate_until_healthy(cfg, log, state,
                                      deadline=time.time() + max(60, cfg.resume_budget))
        if not health["ok"]:
            print("  ✗ no working WARP block right now — nothing was sent",
                  file=sys.stderr)
            return 1

    argv = resume_argv(cfg, session=session, model=model, prompt=prompt)
    log(f"resume: continuing {session or 'the last session'} on "
        f"{ipv6_egress(cfg) or 'unknown block'}")
    rc = subprocess.call(argv, env=opencode_env(cfg))
    if rc == 0:
        with StateLock(cfg.state_lock):
            s = state.load()
            state.save({**s, "resumes": int(s.get("resumes") or 0) + 1,
                        "auto_resume_last": now()})
    return rc


def supervisor_auto_resume(cfg: Config, state: "State", log: "Log") -> bool:
    """OUC_AUTO_RESUME=1: after the wall is cleared, continue the last session.

    Off by default on purpose. If the user is sitting in front of opencode,
    spawning a second session would duplicate their work; `ouc run` is the
    path where the tool owns the process and can be sure it is safe.
    """
    if not cfg.auto_resume:
        return False
    if not (cfg.oc_bin and os.access(cfg.oc_bin, os.X_OK)):
        return False
    s = state.load()
    last = int(s.get("last_wall") or 0)
    if not last or now() - last > 120:
        return False                      # nothing fresh to resume
    if int(s.get("auto_resume_last") or 0) >= last:
        return False                      # this wall was already handled
    lock = os.path.join(cfg.state_dir, "resume.lock")
    try:
        fh = open(lock, "a+")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False                      # another resume is running
    try:
        env = dict(os.environ, OUC_STATE_DIR=cfg.state_dir, OUC_WARP_MODE=cfg.warp_mode)
        kwargs = dict(stdin=subprocess.DEVNULL, env=env, start_new_session=True,
                      stdout=open(os.path.join(cfg.state_dir, "resume.log"), "a"),
                      stderr=subprocess.STDOUT)
        demoted = _demote(cfg)
        if demoted:
            kwargs["preexec_fn"] = demoted
        proc = subprocess.Popen([sys.executable, os.path.abspath(__file__),
                                 "resume", "--reason", "auto"], **kwargs)
        state.update(auto_resume_last=last, auto_resume_pid=proc.pid)
        log(f"auto-resume: continuing the last session (pid {proc.pid})")
        return True
    except Exception as exc:
        log(f"auto-resume: could not start: {exc}", "WARN")
        return False
    finally:
        try:
            fh.close()
        except OSError:
            pass




def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ouc", description=f"{BRAND} {VERSION} controller")
    p.add_argument("--version", action="version", version=f"{VERSION}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_):
        s = sub.add_parser(name, help=help_)
        s.set_defaults(func=fn)
        return s

    s = add("install", cmd_install, "install the unit/supervisor")
    s.add_argument("--portable", action="store_true", help="force portable mode")
    s.add_argument("--proxy", action="store_true",
                   help="proxy mode: no TUN, no firewall (Codespaces/Docker/CI)")
    s.add_argument("--assume-root", action="store_true")

    s = add("uninstall", cmd_uninstall, "remove everything (incl. legacy leftovers)")
    s.add_argument("--assume-root", action="store_true")
    s.add_argument("--keep-state", action="store_true")

    s = add("start", cmd_start, "start the supervisor")
    s.add_argument("--portable", action="store_true")
    add("stop", cmd_stop, "stop the supervisor and remove the kill-switch")

    s = add("status", cmd_status, "show status")
    s.add_argument("--json", action="store_true")

    s = add("supervise", cmd_supervise, "run the supervisor loop")
    s.add_argument("--foreground", action="store_true")

    s = add("hop", cmd_hop, "rotate to a fresh IPv6 /64 now")
    s.add_argument("--wait", type=int, default=90)

    add("repair", cmd_repair, "repair the tunnel and re-arm the firewall")
    add("rescue", cmd_rescue, "force a working network, whatever the state")
    s = add("pin", cmd_pin, "pin opencode's hosts to the tunnel's IPv6 (on|off|status)")
    s.add_argument("action", nargs="?", default="status", choices=["on", "off", "status"])
    add("verify", cmd_verify, "make one real request and report whether the quota works")
    s = add("bridge", cmd_bridge, "run the HTTP->SOCKS5 bridge for proxy mode")
    s.add_argument("--listen", default="")
    s.add_argument("--socks", default="")
    add("doctor", cmd_doctor, "health check")
    add("config", cmd_config, "print effective configuration")

    s = add("killswitch", cmd_killswitch, "on|off|status")
    s.add_argument("action", choices=["on", "off", "status"])

    s = add("scan-once", cmd_scan_once, "one quota-detection pass")
    s.add_argument("--no-rotate", action="store_true")

    s = add("lane", cmd_lane, "launch opencode with the IPv6 pin")
    s.add_argument("args", nargs=argparse.REMAINDER)

    s = add("run", cmd_run,
            "run opencode; on a free-limit wall, wait for a working WARP block "
            "and continue the same session")
    s.add_argument("args", nargs=argparse.REMAINDER)
    s = add("resume", cmd_resume,
            "continue the last (or --session) opencode session on a working block")
    s.add_argument("-s", "--session", default="", help="session id (ses_…)")
    s.add_argument("-m", "--model", default="", help="model, default: the one that was interrupted")
    s.add_argument("-p", "--prompt", default="", help="continuation prompt")
    s.add_argument("--reason", default="manual", help=argparse.SUPPRESS)
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "args", None) is not None and args.cmd == "lane":
        pass
    cfg = Config()
    log = Log(os.path.join(cfg.state_dir, "ouc.log"))
    try:
        if args.cmd == "lane":
            return cmd_lane(args.args, cfg, log)
        if args.cmd == "stop":
            return cmd_stop(argparse.Namespace(portable=False), cfg, log)
        if args.cmd == "start":
            return cmd_start(args, cfg, log)
        return args.func(args, cfg, log)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
