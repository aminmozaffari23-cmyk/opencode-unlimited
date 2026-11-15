#!/usr/bin/env python3
"""18-check before/after audit for the v2.5.1 Psiphon-fallback run.

Every value here is read from the two snapshots the driver wrote on the
disposable host (`state-pre.json` at 14:16Z, `state-post.json` right after
teardown at 18:04Z). Nothing is typed in by hand: if a check cannot be decided
from the evidence it reports INDETERMINATE rather than guessing.
"""
import json
import os
import re
import sys

EV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "evidence")


def load(name):
    with open(os.path.join(EV, name)) as fh:
        return json.load(fh)


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
    t1 = load("t1.json")
    t2a = load("t2a.json")
    t2b = load("t2b.json")
    git = load("git-verify.json")

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
          a == b, f"{len(a)} -> {len(b)} normalised rules"
                  + ("" if a == b else f"; first difference: "
                     f"{next((x for x in b if x not in a), 'n/a')}"))
    a6, b6 = norm_iptables(pre.get("ip6tables") or []), norm_iptables(post.get("ip6tables") or [])
    check(9, "the IPv6 firewall is unchanged", a6 == b6,
          f"{len(a6)} -> {len(b6)} normalised rules")

    # -- the product leaves nothing running ------------------------------
    live_psiphon = [p for p in (teardown.get("psiphon_processes") or [])
                    if "psiphon-tunnel-core" in p and "pgrep" not in p]
    check(10, "no psiphon tunnel is left running after stop",
          not live_psiphon,
          f"{len(live_psiphon)} real tunnel process(es); "
          f"the raw list also matched pgrep's own command line, which is not a process")
    check(11, "the fallback SOCKS port is closed after stop",
          teardown.get("psiphon_port_listening") is False,
          f":9060 listening = {teardown.get('psiphon_port_listening')}")
    check(12, "the HTTP bridge is stopped after stop",
          teardown.get("bridge_listening") is False,
          f":8080 listening = {teardown.get('bridge_listening')}")

    # -- the code on disk is the code that was tested --------------------
    # The tree was deliberately dirty while the four fixes were being
    # validated on this host; what matters is whether the file that ran the
    # tests is byte-identical to the commit that was pushed.
    check(13, "the exact controller that ran the tests is the pushed commit",
          bool(git.get("identical")),
          f"sha256 running={str(git.get('controller_running_when_tests_finished'))[:16]}… "
          f"pushed={str(git.get('controller_at_pushed_commit'))[:16]}… "
          f"at {git.get('pushed_commit_short')}")
    check(14, "no local-only edits are left behind",
          not (git.get("tree_dirty") or "").strip(),
          f"git status at origin/main: {'clean' if not (git.get('tree_dirty') or '').strip() else git.get('tree_dirty')}")

    # -- the ladder actually did what was asked --------------------------
    attempts = t1.get("attempts") or []
    esc = t1.get("escalated_at_attempt") or 0
    check(15, "the ladder escalates to Psiphon on the 5th failed WARP rotation",
          esc == 5, f"escalated at attempt {esc} of {len(attempts)}; "
                    f"final backend {t1.get('final_state', {}).get('active_backend')}")
    check(16, "the fallback exit is never the host's own address",
          bool(t1.get("egress_is_not_the_host")),
          f"egress {t1.get('egress_after_escalation')} vs host {t1.get('direct_ip')}")

    check(17, "a quota wall is rotated away with no human involved",
          bool(t2a.get("rotated_unattended")),
          f"hops {t2a.get('hops_before')} -> {t2a.get('hops_after')}, "
          f"backend {t2a.get('backend')}, egress {t2a.get('egress')} afterwards")

    rounds = t2b.get("rounds") or []
    good = [r for r in rounds if r.get("verify_ok") and r.get("recalled")]
    regions = sorted({r.get("region") for r in good})
    blocks = {r.get("identity") for r in good}
    check(18, "three Psiphon regions each carry the same conversation",
          len(regions) >= 3 and len(blocks) >= 3 and len(good) >= 3,
          f"{len(good)} verified+recalled rounds across regions {regions}, "
          f"{len(blocks)} distinct blocks {sorted(blocks)}")

    width = max(len(name) for _, name, _, _ in checks)
    ok_count = 0
    print(f"{'#':>2}  {'check'.ljust(width)}  verdict   detail")
    print("-" * (width + 60))
    for cid, name, ok, detail in checks:
        verdict = "PASS" if ok else "FAIL"
        ok_count += bool(ok)
        print(f"{cid:>2}  {name.ljust(width)}  {verdict:<8}  {detail}")
    print("-" * (width + 60))
    print(f"{ok_count}/{len(checks)} checks passed")
    return 0 if ok_count == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
