# Phase 4 — where it stands

Written 2026-11-15. Repository `main` @ **`f9210bd`** (then `a5a…` audit commit),
tree clean, `VERSION = "2.6.5"`.

The earlier revisions of this document said Gap #2 had "never run" because
Codespaces returned HTTP 402. That is no longer the constraint: **every phase
now runs on a disposable GitHub Actions root runner** (`workflow_dispatch` on
`live-verify.yml`), which is a genuinely fresh machine each time — a stronger
setup than the Codespace it replaced, because nothing survives between runs.

## Gap #2 is proven. It took five defects to get there.

The tunnel-mode window ran eight times. Each run failed for a *different*
reason, and every one of them was a real defect that no non-root, TUN-less test
could have found. In order:

| # | What the run showed | The actual cause | Fix |
| --- | --- | --- | --- |
| 1 | `armed: guard_mode=None rules=0`, `ks_rc=1` | `install.sh --proxy` records `kill_switch:false`; a guard over an unpinned resolver refuses to arm, so the whole window measured nothing | `OUC_KILLSWITCH=1` opts a host back in at runtime; the `OUC_DISABLE_KILL_SWITCH` veto still wins |
| 2 | every region "matched the host" (WARP's IPv6) | WARP's TUN was still up, so the tunnel core's own dial-outs left through WARP | take WARP's TUN down before probing, not after a success |
| 3 | every region "matched the host" (bare IPv4) | `probe_egress()` probes **directly** in tunnel mode — never through the bridge. The controller was measuring itself and refusing itself as a leak | `probe_egress(proxy_url=…, strict=True)`; the candidate probe goes through the bridge |
| 4 | `psiphon@DE=-` (empty egress), core logs "shutdown by controller" | the tunnel-dialect guard blocks the Psiphon core's relays; only the supervisor switched dialect, and `ouc hop` did not | `_clear_warp_tun_for_psiphon()` performs the §2 handoff: proxy-compatible rules, then the TUN |
| 5 | `EAI_AGAIN opencode.ai`, then `ECONNREFUSED` from opencode | `warp-cli disconnect` removed the resolver with the interface; and in tunnel mode opencode is launched with **no** proxy at all | `mode proxy` instead of `disconnect` (TUN gone, `warp-svc` DNS kept); `opencode_env()` follows the active backend |

## The green window — run `43713887429`

| Step | Measurement |
| --- | --- |
| `baseline` | direct `145.132.91.161`, `iptables_lines=44`, `guard_rule_count=0`, `tun_devices=0` |
| `tun_window` | `up=True`, `links=['CloudflareWARP']`, global IPv6 present, WARP *Connected* |
| `armed` | `pin_rc=0`, `ks_rc=0` "installed", **19 rules**, `guard_mode=tunnel`, `dialect_tunnel=True`, `hosts_pinned=True` |
| `escalated` | `rc=0`, `backend=psiphon`, `guard_mode=proxy` (`dialect_proxy=True`), `pin_lifted=True`, **egress `68.183.170.43`**, region `1` |
| `no_deadlock` | real `ouc verify`: `ok=True`, `walled=False`, **4.084 s**, `ssh_alive=True` |
| `returned` | `rc=0`, `backend=warp`, `guard_mode=tunnel` (guard re-armed), pin back, fresh `/64` `2a09:bac5:9f22:2832`, **182.69 s** |
| `rescued` | `rc=0`, `rules_clean=True`, `hosts_matches_baseline=True`, `iptables_matches=True`, `ip6tables_matches=True` |
| `left_window` | `tun_devices=0`, `hosts/iptables/ip6tables/routes4` **all match baseline**, `guard_rules=0`, `egress_ok=True`, `egress_direct=145.132.91.161` |

Zero deadlock, zero tracebacks, exact baseline return.

## Gap #1 — proven unattended on a real runner (run `43713887429`)

| Step | Measurement |
| --- | --- |
| `escalated` | `rc=0`, region `US`, egress `107.170.249.172` |
| `seeded` | session `ses_e20d15038ffee63UoMTAuRHgkZ`, token `TOKEN-GAP1-45739` |
| `wall_rotated` | **`rotated_unattended=True`** — no command issued: `194.233.168.134/32 → 172.236.209.145/32` via `psiphon@DE`, region 1→2, auto-resume pid 5316` |
| `continuity` | **`recalled=True`**, same session `ses_e20d15038ffee63UoMTAuRHgkZ`, same token `TOKEN-GAP1-45739`, after the wall |

## §3 chaos on a real runner (run `43713887429`)

| Phase | Measurement |
| --- | --- |
| c1 SIGKILL | `recovered=True`, both ports back, egress `162.243.253.73`, verify `True`, supervisor alive `True` |
| c2 squatting | `said_security=True` — typed refusal, no blackhole |
| c3 corrupt state / pid 999999 / stale lock | `tracebacks=0`, `state_valid_after=True` |
| c4 five parallel hops | `ran=1 refused=4`, `tunnels=1`, listeners on :8080 = `1` (`bridges=2` was a `sudo` wrapper and its child — one listener, now recorded as identities not a count) |
| c5 saturation | burned `2` blocks, `bounded=True`, `honoured_budget=True`, rotation `9.4 s` |
| c6 monkey fuzz | `32` commands, `tracebacks=0`, state valid `True`, config valid `True` |

## Tests and suites

**Offline: 78 passed / 0 failed / 11 skipped. pytest: 104 passed, 11 subtests.**

New regression tests, each written against a defect measured live:

| Test | Asserts |
| --- | --- |
| `t92` | a cold Psiphon bootstrap is retried, not blamed |
| `t93` | `ouc stop` reaps an orphan tunnel with no valid pidfile (4 checks) |
| `t94` | `resume --reason auto` picks the verified model (3 checks) |
| `t95` | the tunnel-mode fallback: guard arms and records its dialect, escalation takes WARP's TUN down via `mode proxy` (resolver kept), pin lifted before the probe, opencode gets the bridge while Psiphon carries traffic, return re-arms the full tunnel guard and restores the pin, and a failed fallback leaves WARP's TUN **up** (22 checks) |

## Final gate

| Item | Status |
| --- | --- |
| offline + pytest, 0 failures | ✅ 78/0/11, 104 passed |
| every live test on a fresh machine | ✅ Actions runner, new VM per dispatch |
| 18/18 audit | `lab3/audit.py` **v2** — 18 core checks + 6 chaos checks against `lab3/evidence/43713887429`; re-audit scores **17/18 + 6/6**, the one miss being check 13 (a dirty tree caused by the harness's own marker file, now moved to `/tmp`) |
| evidence in `lab3/evidence/` | ✅ `lab3/evidence/43713887429/` — all 11 JSONs |
| machine deleted | Actions runners are ephemeral by construction; the Codespaces that were replaced were deleted earlier |

## Where everything lives

```
opencode-unlimited/
├── ouc.py                     the single-file controller (2.6.5)
├── tests/run_tests.sh         t01–t95 (offline; t30–t45 are the real-host set)
├── tests/test_runner_v2.py    104 pytest cases
├── lab3/live_driver.py        live phases: pdiag gap1 gap2 c1..c6 t4
├── lab3/warp_revert_guard.sh  dead-man's switch for the TUN window
├── lab3/summarise.py          evidence directory → Markdown table
├── lab3/audit.py              18-check before/after preservation audit (+6 chaos)
├── lab3/evidence/<run-id>/    pulled JSONs, one directory per live run
└── .github/workflows/live-verify.yml   the matrix, dispatchable with -f phases=…
```
