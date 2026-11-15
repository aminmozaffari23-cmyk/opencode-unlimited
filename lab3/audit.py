#!/usr/bin/env python3
"""Before/after audit for a live Phase-4 run.

Every value is read from the snapshot and phase JSONs the driver wrote on the
disposable host; nothing is typed in by hand, and a check whose evidence is
missing reports FAIL with the reason rather than guessing.

Usage: `python3 lab3/audit.py [evidence-dir]`
(default: lab3/evidence, override: OUC_EVIDENCE)

The 18 core checks cover the host baselines (1-12), code identity (13-14),
Gap #1 (15-16) and Gap #2 (17-18). The §3 chaos runs follow as six extra
checks — reported separately so the headline number stays comparable with
earlier phases, never merged to hide a failure.
"""
import json
import os
import re
import sys

EV = (os.environ.get("OUC_EVIDENCE")
      or os.path.join(os.path.dirname(os.path.abspath(__file__)), "evidence"))


def newest_evidence(root):
    """The run directory to audit.

    `lab3/evidence/` holds one directory per live run. Pointing the audit at it
    and getting an empty answer ("6/18") was a documentation trap: choose the
    newest run that actually has the snapshots, and say which one was used.
    """
    if os.path.exists(os.path.join(root, "state-pre.json")):
        return root
    runs = [os.path.join(root, d) for d in os.listdir(root)
            if os.path.isdir(os.path.join(root, d))]
    runs = [r for r in runs if os.path.exists(os.path.join(r, "state-pre.json"))]
    return max(runs, key=os.path.getmtime) if runs else root


EV = newest_evidence(EV)


def load(name, default=None):
    try:
        with open(os.path.join(EV, name)) as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {} if default is None else default


def step(doc, name):
    """One named step out of a phase document, or {} if it never ran."""
    for s in doc.get("steps") or []:
        if s.get("step") == name:
            return s
    return {}


def num(value, default=-1):
    """A shell-derived count, as an int.

    `count_procs()` returns what `pgrep -c` printed, i.e. a string. Comparing
    `"1" == 1` is False, so a check written against an int silently reports a
    product failure that is really a type error in the audit.
    """
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def norm_iptables(lines):
    """Drop counters and timestamps so two `iptables-save` runs can be compared."""
    out = []
    for line in lines:
        line = re.sub(r"\[[0-9]+:[0-9]+\]", "", line)
        line = re.sub(r"^# .*$", "", line).rstrip()
        if line:
            out.append(line)
    return sorted(out)


def main():
    pre = load("state-pre.json")
    post = load("state-post.json")
    teardown = load("t4-teardown.json")
    gap1 = load("gap1.json")
    gap2 = load("gap2.json")

    checks = []

    def check(cid, name, ok, detail):
        checks.append((cid, name, ok, detail))

    # -- the host itself -------------------------------------------------
    check(1, "the machine still has its real internet",
          bool(post.get("direct_ip")) and post.get("direct_ip") == pre.get("direct_ip"),
          f"direct public IP before {pre.get('direct_ip')} / after {post.get('direct_ip')}")
    check(2, "/etc/hosts is byte-identical to before the run",
          pre.get("hosts") == post.get("hosts"),
          f"sha256 {str(pre.get('hosts'))[:16]}… / {str(post.get('hosts'))[:16]}…")
    check(3, "/etc/resolv.conf is byte-identical",
          pre.get("resolv") == post.get("resolv"),
          f"sha256 {str(pre.get('resolv'))[:16]}… / {str(post.get('resolv'))[:16]}…")
    check(4, "the IPv4 routing table is unchanged",
          pre.get("routes4") == post.get("routes4"),
          f"{len(pre.get('routes4') or [])} -> {len(post.get('routes4') or [])} entries")
    check(5, "the IPv6 routing table is unchanged",
          pre.get("routes6") == post.get("routes6"),
          f"{len(pre.get('routes6') or [])} -> {len(post.get('routes6') or [])} entries")
    check(6, "the IPv4 policy-routing rules are unchanged",
          pre.get("rules4") == post.get("rules4"),
          f"{len(pre.get('rules4') or [])} -> {len(post.get('rules4') or [])} rules")
    check(7, "the IPv6 policy-routing rules are unchanged",
          pre.get("rules6") == post.get("rules6"),
          f"{len(pre.get('rules6') or [])} -> {len(post.get('rules6') or [])} rules")
    a, b = norm_iptables(pre.get("iptables") or []), norm_iptables(post.get("iptables") or [])
    check(8, "the IPv4 firewall is unchanged (counters/timestamps ignored)",
          bool(a) and a == b, f"{len(a)} -> {len(b)} normalised rules"
                  + ("" if a == b else f"; first difference: "
                     f"{next((x for x in b if x not in a), 'n/a')}"))
    a6, b6 = norm_iptables(pre.get("ip6tables") or []), norm_iptables(post.get("ip6tables") or [])
    check(9, "the IPv6 firewall is unchanged", bool(a6) and a6 == b6,
          f"{len(a6)} -> {len(b6)} normalised rules")

    # -- the product leaves nothing running ------------------------------
    live_psiphon = [p for p in (teardown.get("psiphon_processes") or [])
                    if "psiphon-tunnel-core" in p and "pgrep" not in p]
    check(10, "no psiphon tunnel is left running after stop",
          teardown.get("psiphon_processes") is not None and not live_psiphon,
          f"{len(live_psiphon)} real tunnel process(es)")
    check(11, "the fallback SOCKS port is closed after stop",
          teardown.get("psiphon_port_listening") is False,
          f":9060 listening = {teardown.get('psiphon_port_listening')}")
    check(12, "the HTTP bridge is stopped after stop",
          teardown.get("bridge_listening") is False,
          f":8080 listening = {teardown.get('bridge_listening')}")

    # -- the code on disk is the code that was tested --------------------
    dirty = [(k, v) for k, v in (("pre", (pre.get("git_dirty") or "").strip()),
                                 ("post", (post.get("git_dirty") or "").strip())) if v]
    check(13, "the host ran a clean, committed tree (no local-only edits)",
          bool(pre.get("git_head")) and not dirty,
          f"git_head {str(pre.get('git_head'))[:12]}; "
          + ("clean at start and end" if not dirty else f"dirty: {dirty}"))
    check(14, "the controller that ran the tests is byte-identical start to end",
          bool(pre.get("controller")) and pre.get("controller") == post.get("controller"),
          f"sha256 {str(pre.get('controller'))[:16]}… / {str(post.get('controller'))[:16]}…")

    # -- Gap #1: unattended recovery -------------------------------------
    wr = step(gap1, "wall_rotated")
    seeded = step(gap1, "seeded")
    cont = step(gap1, "continuity")
    check(15, "a quota wall is rotated away with no human involved",
          bool(wr.get("rotated_unattended"))
          and wr.get("block_after") and wr.get("block_after") != wr.get("block_before")
          and wr.get("backend") == "psiphon",
          f"{wr.get('block_before')} -> {wr.get('block_after')} by {wr.get('last_rotation')}, "
          f"region {wr.get('region_before')} -> {wr.get('region_after')}, "
          f"auto-resume pid {wr.get('auto_resume_pid')}")
    recalled = bool(cont.get("recalled"))
    # Print what actually happened. An earlier version of this line printed the
    # recalled token even when `recalled` was False — a verdict that argues with
    # its own FAIL is worse than no verdict.
    detail16 = (f"session {cont.get('session')} recalled {cont.get('token')} after the wall"
                if recalled else
                f"session {cont.get('session')} did NOT recall {cont.get('token')} "
                f"(resumes={cont.get('resumes')}, "
                f"auto-resume log: {(cont.get('auto_resume_log') or '').strip()[:120] or 'n/a'})")
    check(16, "the same session survives the rotation and recalls its token",
          recalled and cont.get("session") == seeded.get("session")
          and cont.get("token") == seeded.get("token"),
          detail16)

    # -- Gap #2: kill-switch + tunnel <-> Psiphon ------------------------
    no_dead = step(gap2, "no_deadlock")
    esc = step(gap2, "escalated")
    armed = step(gap2, "armed")
    check(17, "the guard arms, then survives the Psiphon transition deadlock-free",
          bool(armed.get("ks_rc") == 0) and len(armed.get("guard_rules") or []) > 0
          and bool(esc.get("dialect_proxy"))
          and bool((no_dead.get("verify") or {}).get("ok"))
          and bool(no_dead.get("ssh_alive")),
          f"armed {len(armed.get('guard_rules') or [])} rules ({armed.get('guard_mode')}), "
          f"proxy dialect for Psiphon {esc.get('dialect_proxy')}, "
          f"verify ok {(no_dead.get('verify') or {}).get('ok')} in {no_dead.get('seconds')}s, "
          f"ssh alive {no_dead.get('ssh_alive')}")
    left = step(gap2, "left_window")
    rsc = step(gap2, "rescued")
    check(18, "the window leaves the host exactly as it found it",
          bool(left.get("iptables_matches")) and bool(left.get("ip6tables_matches"))
          and bool(left.get("hosts_matches_baseline")) and bool(left.get("routes4_matches"))
          and num(left.get("tun_devices")) == 0 and bool(left.get("egress_ok"))
          and bool(rsc.get("rules_clean")),
          f"iptables {left.get('iptables_matches')}, ip6tables {left.get('ip6tables_matches')}, "
          f"hosts baseline {left.get('hosts_matches_baseline')}, routes4 {left.get('routes4_matches')}, "
          f"tun devices {left.get('tun_devices')}, egress ok {left.get('egress_ok')}")

    # -- §3 chaos, reported apart so the headline stays comparable -------
    chaos = []
    c1 = load("chaos1.json")
    chaos.append((19, "a SIGKILLed tunnel is respawned and traffic verified unaided",
                  bool(c1.get("recovered")) and bool(c1.get("supervisor_alive")),
                  f"recovered {c1.get('recovered')}, supervisor alive {c1.get('supervisor_alive')}, "
                  f"egress {(c1.get('after_kill') or {}).get('egress')}"))
    c2 = load("chaos2.json")
    chaos.append((20, "a squatter on 9060/8080 is refused, not trusted",
                  bool((c2.get("refusal") or {}).get("said_security")),
                  f"refusal said security: {(c2.get('refusal') or {}).get('said_security')}, "
                  f"sq_rc {(c2.get('squatter') or {}).get('rc')}"))
    c3 = load("chaos3.json")
    chaos.append((21, "corrupt state, pid 999999 and a stale lock self-heal",
                  num(c3.get("tracebacks"), -1) == 0 and bool(c3.get("state_valid_after")),
                  f"tracebacks {c3.get('tracebacks')}, state valid {c3.get('state_valid_after')}"))
    c4 = load("chaos4.json")
    listeners_8080 = [l for l in (c4.get("bridge_listeners") or []) if ":8080" in l]
    chaos.append((22, "five parallel hops give one tunnel, one bridge, no orphans",
                  num(c4.get("tunnels")) == 1 and num(c4.get("tracebacks"), -1) == 0
                  and bool(c4.get("state_valid"))
                  and len(listeners_8080) <= 1,
                  f"ran {c4.get('ran')} refused {c4.get('refused')}, tunnels {c4.get('tunnels')}, "
                  f"bridges {c4.get('bridges')}, listeners on :8080 {len(listeners_8080)}"))
    c5 = load("chaos5.json")
    chaos.append((23, "saturation skips burned exits and honours the budget",
                  bool(c5.get("bounded")) and bool(c5.get("honoured_budget")),
                  f"burned {len(c5.get('burned') or [])} blocks, rotation "
                  f"{(c5.get('rotation') or {}).get('seconds')}s, bounded {c5.get('bounded')}"))
    c6 = load("chaos6.json")
    chaos.append((24, "monkey fuzzing during a rotation leaves state byte-intact",
                  num(c6.get("tracebacks"), -1) == 0 and bool(c6.get("state_valid"))
                  and bool(c6.get("config_valid")),
                  f"{c6.get('count')} commands, tracebacks {c6.get('tracebacks')}, "
                  f"state valid {c6.get('state_valid')}"))

    def table(rows, title):
        width = max(len(name) for _, name, _, _ in rows)
        print(f"\n{title}")
        print("-" * (width + 60))
        for cid, name, ok, detail in rows:
            print(f"{cid:>2}  {name.ljust(width)}  {'PASS' if ok else 'FAIL':<8}  {detail}")
        print("-" * (width + 60))
        return sum(1 for _, _, ok, _ in rows if ok)

    print(f"evidence: {EV}")
    passed = table(checks, "core (host baselines, code identity, Gap #1, Gap #2)")
    cpassed = table(chaos, "§3 chaos")
    print(f"\n{passed}/{len(checks)} core checks passed "
          f"({cpassed}/{len(chaos)} chaos checks)")
    return 0 if passed == len(checks) and cpassed == len(chaos) else 1


if __name__ == "__main__":
    sys.exit(main())
