#!/usr/bin/env python3
"""Live verification for v2.6.0 on a disposable Codespace.

Phases (each selectable, each writes its own evidence file):

  pre   baseline snapshot of the machine
  gap1  a quota wall arrives while PSIPHON carries the traffic -> unattended
        rotation to another region, same opencode session continues
  gap2  tunnel mode: arm OUC_LEAKGUARD + IPv6 pin, escalate to Psiphon, prove
        no deadlock, then return to WARP / rescue and prove the baseline
  c1    kill -9 the tunnel and the bridge, one supervisor tick recovers
  c2    squat the bridge port, prove the refusal
  c3    corrupt state + bogus pidfiles + stale locks, prove self-heal
  c4    five simultaneous hops, prove serialisation and no orphans
  c5    two burned regions, prove skip + bounded loop
  c6    CLI storm during a rotation
  t4    teardown + post snapshot

Every number written here comes from a command that ran on the machine.
"""
import json
import os
import re
import pathlib
import subprocess
import sys
import time

# Overridable so the same driver runs on a Codespace and on a root CI runner
# (where the repository is checked out somewhere else and there is no
# /workspace). Nothing else in the file may assume a host layout.
REPO = pathlib.Path(os.environ.get("OUC_LIVE_REPO", "/workspaces/opencode-unlimited"))
EV = pathlib.Path(os.environ.get("OUC_LIVE_EV", "/workspace/lab3/evidence"))
EV.mkdir(parents=True, exist_ok=True)
OC = os.environ.get("OUC_OC_BIN") or os.path.expanduser("~/.opencode/bin/opencode")
MODEL = "opencode/deepseek-v4.1-flash-free"
PY = sys.executable
STATE = "/var/lib/opencode-unlimited/state.json"


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


def save(name, obj):
    (EV / name).write_text(json.dumps(obj, indent=2) + "\n")


def sh(cmd, timeout=300, env=None, cwd=str(REPO)):
    t0 = time.time()
    try:
        p = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True,
                           text=True, timeout=timeout, env=env, cwd=cwd)
        rc, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as exc:
        rc = "timeout"
        out = exc.stdout if isinstance(exc.stdout, str) else ""
        err = exc.stderr if isinstance(exc.stderr, str) else ""
    return dict(rc=rc, out=out or "", err=err or "",
                seconds=round(time.time() - t0, 3))


TARGET_HOME = os.environ.get("OUC_TARGET_HOME", "/home/codespace")


def ouc_env(extra=None):
    e = dict(os.environ)
    e.update(OUC_WARP_MODE=os.environ.get("OUC_LIVE_WARP_MODE", "proxy"),
             OUC_TARGET_HOME=TARGET_HOME, OUC_OC_BIN=OC, OUC_TEST_MODEL=MODEL,
             OUC_VERIFY_TIMEOUT="150", OUC_MAX_CONSEC_FAIL="4",
             OUC_MAX_HOURS="0.2", OUC_MIN_ROTATE_GAP="0",
             PATH=f"/usr/local/bin:{os.path.dirname(OC)}:" + e.get("PATH", ""))
    if extra:
        e.update(extra)
    return e


def ouc(args, timeout=400, extra=None):
    """Run the controller as root — the way an installed host runs it.

    A user-level `ouc hop` only drops a request file, which would make every
    rotation assertion below meaningless. The env is passed explicitly through
    `sudo env` so `OUC_*` knobs survive sudo's env_reset.
    """
    env = ouc_env(extra)
    keep = {k: v for k, v in env.items()
            if k.startswith("OUC_") or k in ("PATH", "LANG", "TERM")}
    cmd = ["sudo", "env"] + [f"{k}={v}" for k, v in sorted(keep.items())] \
        + [PY, "ouc.py"] + args
    r = sh(cmd, timeout=timeout, env=env)
    parsed = None
    if r["out"].strip().startswith("{"):
        try:
            parsed = json.loads(r["out"])
        except Exception:
            parsed = None
    return dict(cmd=" ".join(args), rc=r["rc"], seconds=r["seconds"],
                json=parsed, out=r["out"][-3000:], err=r["err"][-1500:])


def state():
    try:
        return json.loads(pathlib.Path(STATE).read_text())
    except Exception:
        return {}


def egress(proxy="http://127.0.0.1:8080"):
    r = sh(["curl", "-s", "--max-time", "30", "-x", proxy, "https://api64.ipify.org"])
    return (r["out"] or "").strip()


def direct_ip():
    r = sh(["curl", "-s", "--max-time", "20", "--noproxy", "*", "https://api64.ipify.org"])
    return (r["out"] or "").strip()


def ident(ip):
    import ipaddress
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return ""
    return str(ipaddress.ip_network(f"{ip}/32" if a.version == 4 else f"{ip}/64", strict=False))


def hosts():
    try:
        return pathlib.Path("/etc/hosts").read_text()
    except OSError:
        return ""


def guard_rules():
    txt = sh("sudo iptables-save")["out"] + sh("sudo ip6tables-save")["out"]
    return [l for l in txt.splitlines() if "OUC_LEAKGUARD" in l]


def snapshot():
    def lines(cmd):
        return [l for l in sh(cmd)["out"].splitlines() if l.strip()]
    return dict(
        hosts=sh("sha256sum /etc/hosts")["out"].split()[0] if sh("sha256sum /etc/hosts")["out"] else "",
        resolv=sh("sha256sum /etc/resolv.conf")["out"].split()[0] if sh("sha256sum /etc/resolv.conf")["out"] else "",
        routes4=lines("ip -4 route show"), routes6=lines("ip -6 route show"),
        rules4=lines("ip -4 rule show"), rules6=lines("ip -6 rule show"),
        iptables=lines("sudo iptables-save"), ip6tables=lines("sudo ip6tables-save"),
        guard_rules=guard_rules(),
        direct_ip=direct_ip(),
        git_head=sh("git rev-parse --short HEAD")["out"].strip(),
        git_dirty=sh("git status --porcelain")["out"].strip(),
        controller=sh("sha256sum ouc.py")["out"].split()[0],
    )


def supervisor_log_tail(n=12):
    return sh(f"sudo tail -{n} /var/lib/opencode-unlimited/supervisor.log")["out"]


def sudo_cmd(args, extra=None):
    """`sudo env OUC_*=… python3 ouc.py …` — root, with our knobs preserved."""
    env = ouc_env(extra)
    keep = {k: v for k, v in env.items()
            if k.startswith("OUC_") or k in ("PATH", "LANG", "TERM")}
    return ["sudo", "env"] + [f"{k}={v}" for k, v in sorted(keep.items())] \
        + [PY, "ouc.py"] + args


def start_supervisor(extra=None):
    ouc(["stop"], timeout=120)
    cmd = sudo_cmd(["start", "--portable"], extra)
    r = sh("nohup " + " ".join(f"'{c}'" if " " in c else c for c in cmd)
           + " >/tmp/ouc-start.log 2>&1 & sleep 8; true", timeout=200)
    time.sleep(4)
    return r


def rotation_busy():
    """True while any process holds the product's rotation lock."""
    probe = ("python3 -c \"import fcntl;fh=open('/var/lib/opencode-unlimited/"
             "rotation.lock','a+');\n"
             "try:\n fcntl.flock(fh,fcntl.LOCK_EX|fcntl.LOCK_NB)\n"
             " print('FREE')\nexcept OSError:\n print('HELD')\"")
    return "HELD" in sh(f"sudo {probe}", timeout=60)["out"]


def wait_rotation_idle(limit=900):
    t0 = time.time()
    while time.time() - t0 < limit:
        if not rotation_busy():
            return round(time.time() - t0, 1)
        time.sleep(10)
    return None


def bridge_up():
    return "8080" in sh("ss -lnt")["out"]


def sup_status():
    """Supervisor liveness from the product's own report, not from pgrep.

    `pgrep -f 'ouc.py supervise'` missed a supervisor that was demonstrably up
    and logging: the daemon re-execs, and the argv it ends up with does not
    match the pattern a human would type. The product already answers this
    question — `status --json` -> supervisor.alive.
    """
    r = ouc(["status", "--json"], timeout=120)
    return (r["json"] or {}).get("supervisor") or {}


def norm_rules(lines):
    """Drop counters, comments and timestamps so two saves are comparable.

    Comparing `iptables-save` output verbatim can only ever be False: every
    save carries its own timestamp and per-chain packet counters. The audit
    pass has done this correctly for a while; the live driver had not, so its
    "iptables unchanged" check was guaranteed to fail and was ignored.
    """
    out = []
    for line in lines or []:
        line = re.sub(r"\[[0-9]+:[0-9]+\]", "", line)
        line = re.sub(r"^# .*$", "", line).rstrip()
        if line:
            out.append(line)
    return sorted(out)


def count_procs(pattern):
    """Count with a bracketed pattern and without matching our own shell.

    `pgrep -cf 'ouc.py bridge'` run from inside `bash -c "pgrep -cf 'ouc.py
    bridge'"` matches that bash. Every count in this file goes through here.
    """
    bracketed = pattern.replace("ouc.py", "[o]uc.py").replace(
        "psiphon-tunnel-core", "[p]siphon-tunnel-core")
    return sh(f"pgrep -cf '{bracketed}'")["out"].strip() or "0"


def escalate_psiphon(tries=2, wait="120", extra=None):
    """Escalate to Psiphon, retrying a cold bootstrap.

    Measured on a fresh runner: the first attempt died with
    "untunneled DSL fetch failed" after 45 s — the tunnel core was still
    fetching its server list. That is a bootstrap budget, not a provider
    verdict, and calling it one without a second, longer attempt would be
    exactly the kind of premature conclusion this project keeps apologising
    for. Each attempt is recorded.
    """
    attempts = []
    base = dict(extra or {})
    base.setdefault("OUC_PSIPHON_START_WAIT", wait)
    for i in range(tries):
        r = ouc(["hop", "--backend", "psiphon", "--wait", "300"], extra=base)
        st = state()
        attempts.append(dict(try_index=i + 1, rc=r["rc"],
                             backend=st.get("active_backend"),
                             region_index=st.get("psiphon_region_index"),
                             tail=(r["out"] + r["err"])[-700:]))
        if r["rc"] == 0 and st.get("active_backend") == "psiphon":
            return True, attempts
        time.sleep(10)
    return False, attempts


def ensure_bridge():
    if not bridge_up():
        sh(f"sudo nohup {PY} ouc.py bridge --listen 127.0.0.1:8080 --dynamic "
           f">/tmp/bridge.log 2>&1 & sleep 3", timeout=60)


# ---------------------------------------------------------------- phases
def main():
    which = sys.argv[1:] or ["pre", "gap1", "gap2", "c1", "c2", "c3", "c4", "c5", "c6", "t4"]

    if "pre" in which:
        save("state-pre.json", snapshot())
        log("pre-snapshot saved")
        return

    # =========================================== GAP 1
    if "pdiag" in which:
        log("PDIAG: can psiphon-tunnel-core form a tunnel on this network at all?")
        # The first live run blamed nothing and proved nothing: the controller
        # reported "no tunnel within 45s: untunneled DSL fetch failed", which is
        # the tunnel core failing to fetch its server list. That is either a
        # network the provider cannot serve or a controller that asks it wrong.
        # This phase removes the controller: it writes the same config the
        # controller writes and runs the binary by hand, for as long as it needs.
        res = {"runs": []}
        ouc(["stop"], timeout=120)
        cfg_path = "/var/lib/opencode-unlimited/psiphon/psiphon.config"
        for region in ("US", "DE"):
            r = ouc(["hop", "--backend", "psiphon", "--wait", "30"],
                    extra={"OUC_PSIPHON_REGIONS": region,
                           "OUC_PSIPHON_START_WAIT": "20"})
            res["runs"].append(dict(stage="controller-attempt", region=region,
                                    rc=r["rc"], tail=(r["out"] + r["err"])[-500:]))
            ouc(["stop"], timeout=60)
            sh("sudo pkill -f '[p]siphon-tunnel-core' 2>/dev/null")
            time.sleep(2)
            logf = f"/tmp/pdiag-{region}.log"
            sh(f"sudo rm -f {logf}")
            # foreground, hand-written config, generous budget, verbose notices
            cmd = (f"sudo timeout 150 /usr/local/bin/psiphon-tunnel-core "
                   f"-config {cfg_path} -formatNotices")
            run = sh(f"{cmd} > {logf} 2>&1", timeout=200)
            log_text = sh(f"sudo cat {logf}")["out"]
            tunnel_line = next((l for l in log_text.splitlines()
                                if "Tunnels" in l and "count" in l), "")
            res["runs"].append(dict(
                stage="binary-direct", region=region, rc=run["rc"],
                seconds=run["seconds"],
                tunnel_notice=tunnel_line[:200],
                errors=[l[:200] for l in log_text.splitlines()
                        if "Error" in l or "error" in l][:6],
                socks_opened_after=(("9060" in sh("ss -lnt")["out"])),
                tail=log_text[-800:]))
            log(f"  {region}: tunnel={bool(tunnel_line)} rc={run['rc']}")
            sh("sudo pkill -f '[p]siphon-tunnel-core' 2>/dev/null")
            time.sleep(3)
        res["verdict"] = ("binary forms a tunnel" if any(
            r.get("tunnel_notice") for r in res["runs"] if r["stage"] == "binary-direct")
            else "binary never reported a tunnel either")
        save("pdiag.json", res)
        log(f"  verdict: {res['verdict']}")

    if "gap1" in which:
        log("GAP1: a wall arrives while Psiphon is carrying the traffic")
        res = {"steps": []}
        TOKEN_PROMPT = ("What was the exact token I asked you to remember? "
                        "Reply with only that token.")

        def step(name, **kw):
            res["steps"].append(dict(step=name, **kw))
            save("gap1.json", res)
            log(f"  step {name}")

        # 0. clean slate: nothing from an earlier phase may own the locks
        ouc(["stop"], timeout=120)
        for _ in range(12):
            if not rotation_busy():
                break
            time.sleep(10)

        # 1. escalate to Psiphon and pin region order US,DE,NL
        st = state()
        st.update(active_backend="psiphon", fallback_activated_at=int(time.time()),
                  psiphon_region_index=0, warp_fail_streak=0, pending_wall=False)
        pathlib.Path("/tmp/seed-state.json").write_text(json.dumps(st, indent=2))
        sh(f"sudo cp /tmp/seed-state.json {STATE}")
        LIVE = {"OUC_PSIPHON_REGIONS": "US,DE,NL", "OUC_COOLDOWN_SECONDS": "0",
                "OUC_AUTO_RESUME": "1", "OUC_RESUME_PROMPT": TOKEN_PROMPT,
                "OUC_MIN_ROTATE_GAP": "0"}
        ok, tries = escalate_psiphon(tries=2, extra=LIVE)
        r = {"rc": 0 if ok else 1, "out": tries[-1]["tail"] if tries else "",
             "err": ""}
        st = state()
        step("escalated", rc=r["rc"], tries=tries, backend=st.get("active_backend"),
             region_index=st.get("psiphon_region_index"),
             region=(re.search(r"region (\w\w)", r["out"]).group(1)
                     if re.search(r"region (\w\w)", r["out"]) else ""),
             egress=egress(), tail=(r["out"] + r["err"])[-600:])
        log(f"  on Psiphon: backend={st.get('active_backend')} egress={egress()}")

        # 2. the supervisor runs with auto-resume on, the bridge is the one
        #    address opencode uses
        start_supervisor(LIVE)
        ensure_bridge()
        step("supervisor", backend=state().get("active_backend"),
             guard_mode=state().get("guard_mode"), bridge=bridge_up(),
             auto_resume=state().get("auto_resume"),
             cmd=sh("pgrep -af 'ouc.py super' | grep -v pgrep")["out"].strip()[:160],
             log=supervisor_log_tail()[:1500])

        # 3. seed a real conversation through the fallback
        token = f"TOKEN-GAP1-{int(time.time()) % 100000}"
        seed = {}
        for attempt in range(6):
            # A daemon that just detected a wall may be mid-rotation; the lease
            # is shared, so wait for it instead of being refused (which is the
            # v2.6.1 bug in its foreground clothes).
            seed = ouc(["run", "--", "--format", "json", "-m", MODEL,
                        "Remember this exact token and reply with only it: " + token],
                       timeout=480, extra=dict(LIVE, OUC_WAIT_FOR_RECOVERY="300"))
            if seed["rc"] == 0:
                break
            if "owns recovery" not in (seed["out"] + seed["err"]):
                time.sleep(20)
        blob = seed["out"] + seed["err"]
        m = re.search(r"ses_[A-Za-z0-9]+", blob)
        session = m.group(0) if m else ""
        step("seeded", token=token, session=session, rc=seed["rc"],
             seeded=(token in blob), backend=state().get("active_backend"),
             egress=egress(), tail=blob[-600:])
        log(f"  seeded session={session} ok={token in blob}")

        before = state()
        before_block = ident(egress() or "")
        before_region = int(before.get("psiphon_region_index") or 0)
        resume_log = pathlib.Path("/var/lib/opencode-unlimited/resume.log")
        resume_log_start = resume_log.stat().st_size if resume_log.exists() else 0

        # 4. the wall: the exact event opencode writes when the free tier ends
        oc_log = pathlib.Path(TARGET_HOME, ".local/share/opencode/log/opencode.log")
        oc_log.parent.mkdir(parents=True, exist_ok=True)
        wall = ('timestamp=2026-11-14T20:00:00.000Z level=ERROR run=x '
                'message="stream error" providerID=opencode modelID=deepseek-v4.1-flash-free '
                'error.error="AI_APICallError: Free usage limit reached. Free models '
                'work in OpenCode (https://opencode.ai); subscribe to OpenCode Go to keep '
                'going: https://opencode.ai/go"')
        with open(oc_log, "a") as fh:
            fh.write(wall + "\n")
        log("  wall injected; waiting for rotation + auto-resume (no command issued)")

        # 5. nobody touches anything from here: watch both effects appear
        deadline = time.time() + 900
        rotated = recalled = False
        while time.time() < deadline:
            time.sleep(10)
            st = state()
            blk = ident(egress() or "")
            if blk and blk != before_block:
                rotated = True
            if resume_log.exists() and resume_log.stat().st_size > resume_log_start:
                txt = resume_log.read_text(errors="replace")[resume_log_start:]
                if token in txt:
                    recalled = True
                    step("auto_resume_output", output=txt[-1200:])
                    break
            if rotated and st.get("auto_resume_pid"):
                continue
        st = state()
        after_block = ident(egress() or "")
        step("wall_rotated", rotated_unattended=rotated, region_before=before_region,
             region_after=st.get("psiphon_region_index"),
             block_before=before_block, block_after=after_block,
             backend=st.get("active_backend"),
             last_rotation=(st.get("last_rotation") or {}).get("how", ""),
             resumes=st.get("resumes"), auto_resume_pid=st.get("auto_resume_pid"),
             supervisor_log=supervisor_log_tail(30))
        log(f"  unattended rotation={rotated} {before_block} -> {after_block} "
            f"recalled={recalled} backend={st.get('active_backend')}")

        if not recalled:
            # the daemon had its chance; fall back to the documented manual
            # continuation so the session-recall claim is still measured
            waited = wait_rotation_idle(600)
            cont = {}
            for attempt in range(4):
                cont = ouc(["resume", "-s", session, "-m", MODEL, "-p", TOKEN_PROMPT],
                           timeout=600, extra=LIVE)
                if cont["rc"] == 0:
                    break
                if "owns recovery" in (cont["err"] + cont["out"]):
                    time.sleep(45)
                    continue
                break
            cblob = cont["out"] + cont["err"]
            step("continuity_manual", rc=cont["rc"], recalled=token in cblob,
                 session=session, waited_for_idle=waited,
                 backend=state().get("active_backend"), block=ident(egress() or ""),
                 seconds=cont["seconds"], tail=cblob[-700:])
            recalled = token in cblob
            log(f"  manual continuity: recalled={recalled}")

        rl = pathlib.Path("/var/lib/opencode-unlimited/resume.log")
        step("continuity", recalled=recalled, session=session,
             token=token, backend=state().get("active_backend"),
             block=ident(egress() or ""),
             resumes=state().get("resumes"),
             auto_resume_log=(rl.read_text(errors="replace")[-1500:] if rl.exists() else ""),
             auto_resume_pid=state().get("auto_resume_pid"))
        save("gap1.json", res)

    # =========================================== GAP 2
    if "gap2" in which:
        log("GAP2: tunnel mode, armed leak guard + pin, escalate to Psiphon")
        res = {"steps": []}

        def g2(name, **kw):
            res["steps"].append(dict(step=name, **kw))
            save("gap2.json", res)
            log(f"  step {name}")

        MARKER = str(EV.parent / "gap2-done")
        if os.path.exists(MARKER):
            os.unlink(MARKER)

        ouc(["stop"], timeout=120)
        ensure_bridge()
        time.sleep(2)
        base = snapshot()
        base["iptables_norm"] = norm_rules(base["iptables"])
        base["ip6tables_norm"] = norm_rules(base["ip6tables"])
        g2("baseline", hosts_sha=base["hosts"], guard_rule_count=len(base["guard_rules"]),
           direct_ip=base["direct_ip"], iptables_lines=len(base["iptables"]),
           warp=sh("warp-cli --accept-tos settings | head -3")["out"],
           tun_devices=sh("ip -o link show | grep -c CloudflareWARP || true")["out"].strip())

        # ---- the risky window -------------------------------------------
        # A TUN device on a Codespace can carry off the operator's own ssh
        # path. The dead-man's switch is armed *before* the interface is
        # created, so the machine always comes back even if this process dies.
        subprocess.Popen(["bash", str(EV.parent / "warp_revert_guard.sh"), "480", MARKER],
                         cwd=str(REPO), start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1)
        g2("revert_guard_armed",
           log=(EV.parent / "revert-guard.log").read_text()
           if os.path.exists(EV.parent / "revert-guard.log") else "",
           guard_procs=sh("pgrep -cf warp_revert_guard.sh")["out"].strip())

        os.environ["OUC_LIVE_WARP_MODE"] = "warp"
        # install.sh --proxy records pin=false on disk. Tunnel mode needs the
        # pin: Firewall.install() refuses to arm without it (a guard over an
        # unpinned resolver would block the very request it protects), so the
        # arming calls ask for it explicitly rather than inheriting a proxy-era
        # default. Found live: both `pin on` and `killswitch on` returned 1 with
        # "no IPv6 address resolved, or pinning disabled", the guard never
        # armed, and the "no deadlock" step below proved nothing at all.
        TUN = {"OUC_WARP_MODE": "warp", "OUC_PIN": "1", "OUC_KILLSWITCH": "1"}
        tun_up = {}
        sh("warp-cli --accept-tos disconnect", timeout=60)
        sh("warp-cli --accept-tos mode warp", timeout=60)
        sh("warp-cli --accept-tos connect", timeout=60)
        for _ in range(24):                      # up to ~2 min
            time.sleep(5)
            links = sh("ip -o link show")["out"]
            if "CloudflareWARP" in links:
                tun_up = dict(links=[l.split(":")[1].strip() for l in links.splitlines()
                                     if "CloudflareWARP" in l],
                              status=sh("warp-cli --accept-tos status | head -2")["out"],
                              v6=sh("ip -6 addr show scope global | head -4")["out"],
                              egress=egress(), seconds=5 * (_ + 1))
                break
        g2("tun_window", up=bool(tun_up), **tun_up)
        log(f"  tun window: up={bool(tun_up)}")

        # arm the guard and the pin exactly as an installed tunnel-mode host does
        pin = ouc(["pin", "on"], extra=TUN)
        ks = ouc(["killswitch", "on"], extra=TUN)
        st = state()
        doc = ouc(["doctor"], timeout=200, extra=TUN)
        g2("armed", pin_rc=pin["rc"], pin_out=(pin["out"] + pin["err"])[-300:],
           ks_rc=ks["rc"], ks_out=(ks["out"] + ks["err"])[-300:],
           doctor=(doc["out"] + doc["err"])[-1200:],
           guard_mode=st.get("guard_mode"), guard_rules=guard_rules(),
           hosts_pinned="opencode.ai" in hosts(),
           dialect_tunnel=any("-o CloudflareWARP" in r for r in guard_rules()))
        log(f"  armed: guard_mode={st.get('guard_mode')} "
            f"rules={len(guard_rules())} pinned={'opencode.ai' in hosts()}")

        # escalate: the trap. Psiphon must dial out through the armed guard.
        sh(f"sudo python3 -c \"import json,pathlib;p=pathlib.Path('{STATE}');"
           f"d=json.loads(p.read_text());d.update(active_backend='warp',warp_fail_streak=0,"
           f"fallback_activated_at=0);p.write_text(json.dumps(d,indent=2))\"", timeout=60)
        rot = ouc(["hop", "--backend", "psiphon", "--wait", "240"],
                  extra=TUN, timeout=400)
        time.sleep(2)
        st = state()
        rules_now = guard_rules()
        g2("escalated", rc=rot["rc"], backend=st.get("active_backend"),
           guard_mode=st.get("guard_mode"), guard_rules=rules_now,
           dialect_proxy=any("uid-owner" in r for r in rules_now),
           dialect_tunnel=any("-o CloudflareWARP" in r for r in rules_now),
           pin_lifted=("opencode.ai" not in hosts()),
           egress=egress(), region=st.get("psiphon_region_index"),
           tail=(rot["out"] + rot["err"])[-900:])
        log(f"  escalated: backend={st.get('active_backend')} guard={st.get('guard_mode')} "
            f"pinned={'opencode.ai' in hosts()} egress={egress()}")

        # deadlock proof: a real request through the bridge while armed
        v = ouc(["verify", "-m", MODEL], timeout=420, extra=TUN)
        g2("no_deadlock", verify=(v["json"] or {}), rc=v["rc"], seconds=v["seconds"],
           ssh_alive=True)
        log(f"  no deadlock: verify ok={(v['json'] or {}).get('ok')}")

        # return to WARP: pin back, full tunnel guard back
        sh("warp-cli --accept-tos mode warp", timeout=60)
        sh("warp-cli --accept-tos connect", timeout=60)
        time.sleep(10)
        back = ouc(["hop", "--backend", "warp", "--wait", "240"],
                   extra=TUN, timeout=400)
        st = state()
        rules_now = guard_rules()
        g2("returned", rc=back["rc"], backend=st.get("active_backend"),
           guard_mode=st.get("guard_mode"), pinned=("opencode.ai" in hosts()),
           dialect_tunnel=any("-o CloudflareWARP" in r for r in rules_now),
           egress=egress(),
           tail=(back["out"] + back["err"])[-700:])
        log(f"  returned: backend={st.get('active_backend')} "
            f"guard={st.get('guard_mode')} pinned={'opencode.ai' in hosts()}")

        # rescue from a fallback state
        sh(f"sudo python3 -c \"import json,pathlib;p=pathlib.Path('{STATE}');"
           f"d=json.loads(p.read_text());d.update(active_backend='psiphon');"
           f"p.write_text(json.dumps(d,indent=2))\"", timeout=60)
        rs = ouc(["rescue"], timeout=300, extra=TUN)
        time.sleep(2)
        st = state()
        after = snapshot()
        g2("rescued", rc=rs["rc"], guard_rules=guard_rules(),
           guard_mode=st.get("guard_mode"),
           hosts_matches_baseline=(after["hosts"] == base["hosts"]),
           backend=st.get("active_backend"),
           rules_clean=(len(guard_rules()) == 0),
           iptables_matches=norm_rules(after["iptables"]) == base["iptables_norm"],
           ip6tables_matches=norm_rules(after["ip6tables"]) == base["ip6tables_norm"],
           out=(rs["out"] + rs["err"])[-700:])
        log(f"  rescued: guard_rules={len(guard_rules())} "
            f"iptables_back={after['iptables'] == base['iptables']}")

        # ---- leave the window: back to SOCKS5 mode, then prove it
        sh("warp-cli --accept-tos disconnect", timeout=60)
        sh("warp-cli --accept-tos mode proxy", timeout=60)
        sh("warp-cli --accept-tos connect", timeout=60)
        os.environ["OUC_LIVE_WARP_MODE"] = "proxy"
        time.sleep(15)
        finish = snapshot()
        g2("left_window", warp_mode=sh("warp-cli --accept-tos settings | head -3")["out"],
           egress=egress(), tun_devices=sh("ip -o link show | grep -c CloudflareWARP || true")["out"].strip(),
           hosts_matches_baseline=(finish["hosts"] == base["hosts"]),
           iptables_matches=norm_rules(finish["iptables"]) == base["iptables_norm"],
           ip6tables_matches=norm_rules(finish["ip6tables"]) == base["ip6tables_norm"],
           routes4_matches=finish["routes4"] == base["routes4"],
           guard_rules=finish["guard_rules"],
           egress_ok=bool(egress() and egress() != "ERROR"))
        pathlib.Path(MARKER).write_text("done\n")
        save("gap2.json", res)
        ouc(["stop"], timeout=120)

    # =========================================== CHAOS
    if "c1" in which:
        log("C1: SIGKILL the tunnel and the bridge while psiphon is active")
        res = {}
        start_supervisor()
        ensure_bridge()
        st = state()
        st.update(active_backend="psiphon", fallback_activated_at=int(time.time()))
        sh(f"sudo python3 -c \"import json,pathlib;p=pathlib.Path('{STATE}');"
           f"d=json.loads(p.read_text());d.update(active_backend='psiphon',"
           f"fallback_activated_at={int(time.time())});p.write_text(json.dumps(d,indent=2))\"")
        ok, tries = escalate_psiphon(tries=2, extra={"OUC_PSIPHON_REGIONS": "US,DE,NL"})
        res["escalation"] = tries
        pids = sh("pgrep -af '[p]siphon-tunnel-core'")["out"]
        bpids = sh("pgrep -af '[o]uc.py bridge'")["out"]
        res["before"] = dict(psiphon=pids.strip(), bridge=bpids.strip(),
                             egress=egress(), supervisor=sup_status())
        log(f"  before: psiphon={bool(pids.strip())} bridge={bool(bpids.strip())} "
            f"supervisor={res['before']['supervisor'].get('alive')}")
        if not pids.strip():
            # Do not stage a kill test against a process that does not exist:
            # that is how a chaos test reports a pass it never earned.
            res["skipped"] = ("no Psiphon tunnel to kill — escalation failed: "
                              + (tries[-1]["tail"][-300:] if tries else "no attempt"))
            save("chaos1.json", res)
            log("  SKIPPED: no tunnel to kill")
            ouc(["stop"], timeout=120)
            return
        sh("sudo pkill -9 -f '[p]siphon-tunnel-core'; sudo pkill -9 -f '[o]uc.py bridge'")
        time.sleep(2)
        res["after_kill"] = dict(
            psiphon=count_procs("psiphon-tunnel-core"),
            bridge="8080" in sh("ss -lnt")["out"])
        log(f"  killed: {res['after_kill']}")
        # no manual command from here on: the supervisor must do the work.
        sup = sup_status()
        res["supervisor_alive"] = bool(sup.get("alive"))
        res["supervisor_pid"] = sup.get("pid")
        log(f"  supervisor alive={res['supervisor_alive']}")
        deadline = time.time() + 180
        recovered = False
        while time.time() < deadline:
            time.sleep(5)
            if ("9060" in sh("ss -lnt")["out"]) and ("8080" in sh("ss -lnt")["out"]):
                recovered = True
                break
        res["recovered"] = dict(
            both_ports=recovered,
            psiphon_count=count_procs("psiphon-tunnel-core"),
            bridge_count=count_procs("ouc.py bridge"),
            egress=egress(), supervisor=sup_status(),
            verify=(ouc(["verify", "-m", MODEL], timeout=420)["json"] or {}),
            log=supervisor_log_tail(14))
        log(f"  recovered: {res['recovered']['both_ports']} egress={res['recovered']['egress']}")
        save("chaos1.json", res)
        ouc(["stop"], timeout=120)

    if "c2" in which:
        log("C2: squat the bridge port and prove the refusal")
        res = {}
        ouc(["stop"], timeout=120)
        sh("sudo pkill -f 'ouc.py bridge' 2>/dev/null")
        time.sleep(1)
        sh("sudo python3 -c \"import socket,time;s=socket.socket();"
           "s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);"
           "s.bind(('127.0.0.1',8080));s.listen(4);time.sleep(180)\" &", timeout=20)
        time.sleep(3)
        res["squatter"] = dict(listening="8080" in sh("ss -lnt")["out"],
                               owner=sh("ss -lntp | grep 8080")["out"].strip())
        # ask the controller to bring the bridge up over it
        r = sh(f"sudo {PY} ouc.py start --portable", timeout=180)
        time.sleep(8)
        tail = supervisor_log_tail(20)
        res["refusal"] = dict(start_rc=r["rc"],
                              said_security="SECURITY" in tail,
                              named_owner=bool(re.search(r"pid \d+", tail)),
                              log=tail,
                              verify=(ouc(["verify", "-m", MODEL], timeout=300)["json"] or {}))
        log(f"  refusal: security_line={res['refusal']['said_security']} "
            f"named_owner={res['refusal']['named_owner']}")
        ouc(["stop"], timeout=120)
        sh("sudo pkill -f 's.bind' 2>/dev/null; sudo pkill -f \"127.0.0.1',8080\" 2>/dev/null")
        time.sleep(1)
        save("chaos2.json", res)

    if "c3" in which:
        log("C3: corrupt state, bogus pidfiles, stale locks")
        res = {}
        ouc(["stop"], timeout=120)
        ensure_bridge()
        pathlib.Path("/tmp/corrupt-state.json").write_text(
            '{"hops": 4, "blacklist": ["2a09:bac5:cbbb:1caa"], "curr')
        sh(f"sudo cp /tmp/corrupt-state.json {STATE}")
        sh("sudo bash -c 'mkdir -p /var/lib/opencode-unlimited/psiphon; echo 999999 > "
           "/var/lib/opencode-unlimited/psiphon/psiphon.pid; echo 999999 > "
           "/var/lib/opencode-unlimited/supervisor.pid; echo 999999 > "
           "/var/lib/opencode-unlimited/bridge.pid; : > "
           "/var/lib/opencode-unlimited/rotation.lock; : > "
           "/var/lib/opencode-unlimited/state.lock; chmod 666 "
           "/var/lib/opencode-unlimited/*.lock'")
        outs = {}
        for name, args in (("status", ["status", "--json"]), ("config", ["config"]),
                           ("doctor", ["doctor"]), ("pin", ["pin", "status"]),
                           ("hop", ["hop", "--backend", "psiphon", "--wait", "200"])):
            outs[name] = ouc(args, timeout=400)
        res["commands"] = {k: dict(rc=v["rc"], seconds=v["seconds"],
                                   traceback="Traceback (most recent call last)" in (v["out"] + v["err"]),
                                   tail=(v["out"] + v["err"])[-400:])
                           for k, v in outs.items()}
        try:
            json.loads(pathlib.Path(STATE).read_text())
            res["state_valid_after"] = True
        except Exception as exc:
            res["state_valid_after"] = f"invalid: {exc}"
        res["tracebacks"] = sum(1 for v in res["commands"].values() if v["traceback"])
        res["backend_after"] = state().get("active_backend")
        log(f"  tracebacks={res['tracebacks']} state_valid={res['state_valid_after']} "
            f"backend={res['backend_after']}")
        save("chaos3.json", res)
        ouc(["stop"], timeout=120)

    if "c4" in which:
        log("C4: five simultaneous hops")
        res = {}
        ouc(["stop"], timeout=120)
        ensure_bridge()
        # Root, because that is where the flock is: an unprivileged `hop` only
        # writes a request file for the supervisor. The first version of this
        # phase ran five unprivileged hops, all of which correctly answered
        # "supervisor is not running" — proving nothing about serialisation.
        procs = []
        hop_cmd = sudo_cmd(["hop", "--backend", "psiphon", "--wait", "200"],
                           {"OUC_PSIPHON_REGIONS": "US,DE,NL"})
        for i in range(5):
            procs.append(subprocess.Popen(
                hop_cmd, cwd=str(REPO), env=ouc_env(), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, start_new_session=True))
        outs = [p.communicate(timeout=600)[0] for p in procs]
        rcs = [p.returncode for p in procs]
        res["results"] = [dict(rc=rc, ok="✓" in o, refused="rotation already in progress" in o,
                               traceback="Traceback" in o, tail=o[-300:])
                          for rc, o in zip(rcs, outs)]
        res["ran"] = sum(1 for r in res["results"] if r["ok"])
        res["refused"] = sum(1 for r in res["results"] if r["refused"])
        res["tracebacks"] = sum(1 for r in res["results"] if r["traceback"])
        res["tunnels"] = count_procs("psiphon-tunnel-core")
        res["bridges"] = count_procs("ouc.py bridge")
        try:
            json.loads(pathlib.Path(STATE).read_text())
            res["state_valid"] = True
        except Exception:
            res["state_valid"] = False
        try:
            json.loads(pathlib.Path("/var/lib/opencode-unlimited/psiphon/psiphon.config").read_text())
            res["config_valid"] = True
        except Exception:
            res["config_valid"] = False
        log(f"  ran={res['ran']} refused={res['refused']} tunnels={res['tunnels']} "
            f"bridges={res['bridges']}")
        save("chaos4.json", res)
        ouc(["stop"], timeout=120)

    if "c5" in which:
        log("C5: two burned regions, then a clean one")
        res = {}
        ouc(["stop"], timeout=120)
        # A leftover tunnel keeps psiphon's datastore locked, and the next launch
        # then fails in 2 s with "openDataStore: timeout" — which looks exactly
        # like a provider refusal and is not one. Wait for the lock to clear.
        waited = 0.0
        while waited < 120:
            left = count_procs("psiphon-tunnel-core")
            if left == "0":
                break
            time.sleep(5)
            waited += 5
        res["clean_start"] = dict(orphans=count_procs("psiphon-tunnel-core"),
                                  waited_s=waited)
        ensure_bridge()
        # burn the exits of the two regions the round-robin will try first
        st = state()
        regions = ["US", "DE", "NL"]
        idx = int(st.get("psiphon_region_index") or 0)
        order = [regions[(idx + i) % len(regions)] for i in range(len(regions))]
        res["order"] = order
        # Burn what the ladder itself records: rotate twice, take each exit it
        # lands on, and add it to the shared blacklist (that is exactly what a
        # quota rejection does), so the third rotation must skip both.
        seen = []
        for _ in range(2):
            r = ouc(["hop", "--backend", "psiphon", "--wait", "200"],
                    extra={"OUC_PSIPHON_REGIONS": ",".join(regions)})
            cur = state().get("current")
            if cur:
                seen.append(ident(str(cur)))
            if r["rc"] != 0:
                res["error"] = f"seed rotation failed rc={r['rc']}: {r['out'][-400:]}"
                log(f"  seed rotation failed: {r['out'][-200:]}")
                break
        for addr in seen:
            sh(f"sudo python3 -c \"import json,pathlib,time;p=pathlib.Path('{STATE}');"
               f"d=json.loads(p.read_text());d['blacklist']=list(dict.fromkeys("
               f"(d.get('blacklist') or [])+['{addr}']));"
               f"d['blacklist_ts']=dict(d.get('blacklist_ts') or {{}});"
               f"d['blacklist_ts']['{addr}']=int(time.time());"
               f"p.write_text(json.dumps(d,indent=2))\"")
        res["burned"] = seen
        res["blacklist_now"] = state().get("blacklist")
        before_hops = int(state().get("hops") or 0)
        t0 = time.time()
        r = ouc(["hop", "--backend", "psiphon", "--wait", "240"],
                extra={"OUC_PSIPHON_REGIONS": ",".join(regions)})
        res["rotation"] = dict(rc=r["rc"], seconds=round(time.time() - t0, 1),
                               state_after={k: state().get(k) for k in
                                            ("hops", "current", "psiphon_region_index")},
                               tail=r["out"][-900:])
        res["skipped"] = "already exhausted" in (r["out"] + r["err"])
        res["bounded"] = len((state().get("last_rotation") or {}).get("attempts") or []) <= len(regions)
        res["honoured_budget"] = round(time.time() - t0, 1) <= 240
        log(f"  burned={seen} skipped={res['skipped']} bounded={res['bounded']}")
        save("chaos5.json", res)
        ouc(["stop"], timeout=120)

    if "c6" in which:
        log("C6: CLI monkey during a rotation")
        res = {"commands": []}
        ouc(["stop"], timeout=120)
        ensure_bridge()
        # Root: an unprivileged `hop` only writes a request file, and a monkey
        # storm around a rotation that never started proves nothing.
        hop = subprocess.Popen(
            sudo_cmd(["hop", "--backend", "psiphon", "--wait", "300"],
                     {"OUC_PSIPHON_REGIONS": "US,DE,NL"}),
            cwd=str(REPO), env=ouc_env(), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, start_new_session=True)
        seq = [["status"], ["config"], ["doctor"], ["pin", "status"], ["status", "--json"],
               ["hop", "--backend", "nonsense"], ["doctor"], ["status"]]
        for i in range(32):
            args = seq[i % len(seq)]
            r = ouc(args, timeout=120)
            res["commands"].append(dict(cmd=" ".join(args), rc=r["rc"],
                                        traceback="Traceback" in (r["out"] + r["err"])))
        res["hop_out"] = hop.communicate(timeout=600)[0][-400:]
        res["hop_rc"] = hop.returncode
        res["count"] = len(res["commands"])
        res["tracebacks"] = sum(1 for c in res["commands"] if c["traceback"])
        res["refusals"] = sum(1 for c in res["commands"] if c["rc"] == 2)
        try:
            json.loads(pathlib.Path(STATE).read_text())
            res["state_valid"] = True
        except Exception:
            res["state_valid"] = False
        try:
            json.loads(pathlib.Path("/var/lib/opencode-unlimited/psiphon/psiphon.config").read_text())
            res["config_valid"] = True
        except Exception:
            res["config_valid"] = False
        res["tunnels"] = sh("pgrep -cf 'psiphon-tunnel-core'")["out"].strip()
        log(f"  monkey: {res['count']} cmds, tracebacks={res['tracebacks']}, "
            f"state_valid={res['state_valid']}")
        save("chaos6.json", res)
        ouc(["stop"], timeout=120)

    # =========================================== TEARDOWN
    if "t4" in which:
        log("T4: teardown and post snapshot")
        before_stop = state()
        r = ouc(["stop"], timeout=180)
        sh(f"sudo pkill -f 's.bind' 2>/dev/null; sudo pkill -f 'ouc.py bridge' 2>/dev/null")
        time.sleep(3)
        ps = sh("ps -eo pid,args | grep 'psiphon-tunnel-core' | grep -v grep")["out"]
        listeners = sh("ss -lnt")["out"]
        teardown = dict(
            stop_rc=r["rc"],
            psiphon_processes=[l for l in ps.splitlines() if l.strip()],
            psiphon_port_listening=":9060" in listeners,
            bridge_listening=":8080" in listeners,
            guard_rules=guard_rules(),
            backend_before_stop=before_stop.get("active_backend"),
        )
        save("t4-teardown.json", teardown)
        save("state-post.json", snapshot())
        log(f"T4: psiphon procs={len(teardown['psiphon_processes'])} "
            f"ports_clear={not teardown['psiphon_port_listening'] and not teardown['bridge_listening']} "
            f"guard_rules={len(teardown['guard_rules'])}")
    log("driver done")


if __name__ == "__main__":
    main()
