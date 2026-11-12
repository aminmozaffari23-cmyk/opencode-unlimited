# TEST_RESULTS.md — opencode-unlimited 2.3.1

Everything below was **executed on a real machine**, not reviewed on paper.
The 2.0.1/2.1.0 tunnel-mode results are kept further down as history.

## 2.3.1 — fresh GitHub Codespace, full install, real requests

This section answers one question: **does a person who clones the private repo
into a brand-new cloud machine get a working install?** Not "does it look
right" — the suite was executed there, and opencode really answered through
the tunnel.

| Item | Value |
|---|---|
| Date of the run | 2026-11-12 |
| Machine | GitHub Codespace `basicLinux32gb` (2 vCPU / 8 GB), Ubuntu 24.04.5, user `codespace` |
| Code | `github.com/aminmozaffari23-cmyk/opencode-unlimited` @ `main` (private) |
| Cloudflare WARP | `cloudflare-warp` 2026.8.2115.0, `warp-svc` from apt, `mode proxy`, `proxy port 40000` |
| opencode | 1.19.5 (`~/.opencode/bin/opencode`, installed inside the Codespace) |
| Install command | `sudo bash install.sh --proxy --portable` |
| Offline suite | **39 passed, 0 failed, 0 skipped** |
| Real requests | `ouc hop` (rotation), `ouc verify` (verification), `ouc run` (end-to-end answer) |
| Harness fix included | `OUC_WARP_MODE=warp` forced in the test sandbox, `install.sh --systemd` |

```
results: 39 passed  0 failed  0 skipped        # bash tests/run_tests.sh --offline
```

### The honest story of this run (including the two failures)

1. **First attempt: 33 passed / 5 failed** (t10, t11, t15, t19, t27). Diagnosis:
   every test runs inside `sb()`, which inherited the Codespace's own
   `CODESPACES=true`. `Config._resolve_warp_mode()` therefore chose proxy mode,
   so the pin / kill-switch / repair assertions tested a code path that is
   deliberately disabled in proxy mode and silently did nothing (t27 reported
   `"enabled": false` and wrote no `27-pin.log`). Running the suite with `sudo`
   changed nothing — `CODESPACES` is still set. **Harness artifact, not a product
   bug.** Fix: `sb()` exports `OUC_WARP_MODE=warp`; `install.sh` grew a
   `--systemd` flag so t19 can force a unit on a machine that normally would use
   portable mode.
2. **Then `ouc run` crashed** with
   `PermissionError: [Errno 13] /var/lib/opencode-unlimited/state.lock`. That one
   was **real**: the installer runs as root and left the state directory
   root-owned, while `ouc run` runs as you. Fixed in this version (installer
   hands the state dir to `SUDO_UID`, lock file is created 0666, and the failure
   is now one readable line instead of a traceback — test `t51`).
3. **After both fixes, in the Codespace:** 39/0/0, and the real request answered.

### Live evidence (Codespace, 2026-11-12 ~07:35–07:45 UTC)

Installer:

```
✓ state dir owned by codespace — daily commands work without sudo
✓ warp mode: proxy (no TUN) — SOCKS5 127.0.0.1:40000
✓ http bridge: http://127.0.0.1:8080 (opencode uses it)
✓ supervisor: running
```

One real rotation, then a verification that is a real model request — not a
`curl` probe:

```
$ ouc hop
  old block: 2a09:bac5:cbbd:1d37
  new block: 2a09:bac5:cbbc:154b
  ✓ rotated in 11.29s — resend your message

$ ouc verify
{"ran": true, "ok": true, "walled": false,
 "detail": "opencode answered on the new block",
 "model": "opencode/flint-40b-free",
 "ipv6_egress": "2a09:bac5:cbbc:154b::21f:6d"}
```

The end-to-end path a user actually takes (`ouc run` = opencode + supervision,
traffic through the local bridge to WARP):

```
$ ouc run -- "Reply with exactly: CSPACE-OK"
  ouc run — opencode under supervision (auto-resume x3)
> build · big-pickle
CSPACE-OK
```

Blocks used in this session (all fresh, all real): `2a09:bac5:cbbd:1d37` →
`2a09:bac5:cbbc:154b` → (supervisor rotation) `2a09:bac5:cbb9:3228`.

### What this run does *not* claim

- The wall was never hit in the Codespace, so the auto-resume path there ran
  only through the fake-opencode tests (`t46`–`t50`), not against a real quota
  wall. The real wall → resume evidence is the 2.3.0 live run below.
- TUN mode, the kill-switch and the IPv6 pin are **not** exercised in a
  Codespace by design: there is no usable TUN and no `CAP_NET_ADMIN` there.
  They are covered by the offline tests with forced `OUC_WARP_MODE=warp`.
- Rotation latency in a Codespace (11.3 s for `ouc hop`) includes the proxy
  re-connect wait; the sandbox measured 0.7–1.0 s for the same operation.

## 2.2.0 — proxy mode (no TUN), verified live

| Item | Value |
|---|---|
| Date of the run | 2026-11-08 |
| Host | Debian GNU/Linux 13 (trixie) container, IPv6 enabled, **no firewall changes** |
| Python | 3.13 (stdlib only) |
| Cloudflare WARP | `warp-cli` 2026.8.2115.0, `warp-svc` active, `mode proxy`, `proxy port 40000` |
| opencode | 1.19.5 (`~/.opencode/bin/opencode`) |
| Install command | `sudo bash install.sh --proxy --portable` |
| Offline suite | **33 passed, 0 failed, 0 skipped** (`--offline`, 152 s) |
| Live proxy test | **t45 PASS** — rotation proved by a real request through the HTTP bridge |
| Raw evidence | `tests/logs/` (`runner.t4*.log`, `41.raw`–`45.raw`, `runner.FINAL.log`) |

```
results: 33 passed  0 failed  0 skipped        # bash tests/run_tests.sh --offline
PASS t45_real_proxy_rotation                   # bash tests/run_tests.sh --select=t45
```

### What the live run actually showed

Installer, on a container where TUN and iptables are off-limits:

```
✓ proxy mode (no TUN needed): WARP SOCKS5 + local HTTP bridge
✓ warp tunnel: connected
✓ supervisor: running (pid 14467)
✓ warp mode: proxy (no TUN) — SOCKS5 127.0.0.1:40000
✓ http bridge: http://127.0.0.1:8080 (opencode uses it)
✓ kill-switch: off (disabled at install time)
```

One manual rotation, measured end to end (`t45`, 2026-11-08 18:13 UTC):

```
old block: 2a09:bac1:76a0:dba8
new block: 2a09:bac5:b044:1caa
✓ rotated in 12.52s — resend your message
last_rotation.ok=true verified=true
egress after rotation, through the HTTP bridge: 2a09:bac5:b044:1caa::2db:f2
```

An automatic rotation, triggered by a genuine quota line written into
opencode's own log, was picked up and completed without a human:

```
rotation start (quota-wall); egress=2a09:bac5:b042:3705::57b:4
WARN rotate-keys#1: 2a09:bac1:76c0:dba8 did not restore the quota — rotating again
rotation done in 12.6s via rotate-keys#2: 2a09:bac5:b042:3705 -> 2a09:bac5:b043:1d37 (verified with a real request)
```

Note the rejected first attempt: the recycled block was **refused because the
verification request hit the wall**, which is the whole point of verifying with
a real request instead of trusting the address change.

### The five new proxy tests

| Test | What it proves |
|---|---|
| `t40_proxy_mode_config` | `--proxy` resolves to `warp mode proxy` + port, no TUN, no firewall, kill-switch and pin off **with a recorded reason** |
| `t41_proxy_rotation_verified` | a rotation in proxy mode goes through `rotate-keys`, the fake wall is detected, the new block is verified, and the rotation is invisible to the user's internet |
| `t42_bridge_end_to_end` | a real HTTP request → `CONNECT` → SOCKS5 → origin server returns `bridge-works` (no mocks: a real http.server, a real SOCKS5 helper, the real bridge) |
| `t43_proxy_status_and_doctor` | `status --json` exposes `warp_mode: proxy` + ports; `doctor` prints the bridge, the egress family and the exact `HTTPS_PROXY=…` line opencode needs |
| `t44_proxy_installer` | `install.sh --proxy` records proxy mode and makes **zero** calls to `iptables` (counted via a fake `iptables` in `PATH`) |
| `t45_real_proxy_rotation` | live: a genuine `/64` rotation on a real WARP tunnel, verified by a request, measured at 13 s |

### Bugs this round found (and the test that now catches each)

| Symptom seen live | Cause | Guard |
|---|---|---|
| `warp tunnel: not connected`, `Unable — Another process is bound to the proxy port` | two `warp-svc` daemons (manual + systemd) on port 40000 | `install.sh` now trusts an existing registration and waits longer; runbook notes `pkill -x warp-svc` before `systemctl restart` |
| rotation "succeeded" while the block never changed | probes ran unproxied and compared the container's own IPv4 | `probe_egress` and the stdlib fallback use the proxy; `rotate()` ensures the bridge first (`t41`) |
| two supervisors, two bridges after re-install | `pkill -f "ouc.py supervise"` never matched `start --portable` | fixed pattern + `/run` lock (`t13`, `t20`) |
| `rotation already in progress` on a manual `hop` | supervisor was mid-rotation and the CLI raced it | reported honestly, retried by `t45` |
| install reported a dead tunnel although it reconnected 3 s later | registration deleted before the reconnect could finish | `registration show` checked first |

### Not re-run in this session

`t30`–`t34` are the tunnel-mode live tests (they require a TUN device and
iptables); they passed 5/5 on a TUN-capable host and are unchanged in this
release. The offline suite covers every code path they touch, and `t45` covers
the live proxy path.

---

# TEST_RESULTS.md — opencode-unlimited 2.0.1 (history)

Everything below was **executed on a real machine**, not reviewed on paper.

| Item | Value |
|---|---|
| Date of the run | 2026-11-08 |
| Host | Debian GNU/Linux 13 (trixie), kernel with `/dev/net/tun`, IPv6 enabled |
| Python | 3.13.16 (stdlib only, no third-party packages) |
| Cloudflare WARP | `warp-cli` 2026.8.2115.0, `warp-svc` active, mode `warp` |
| Runner | `sudo bash tests/run_tests.sh` |
| Result | **28 passed, 0 failed, 0 skipped** in 284 s |
| Raw evidence | `tests/logs/` — `runner.FINAL.log` (whole run) plus one file per test |

```
results: 28 passed  0 failed  0 skipped
```

Reproduce exactly:

```bash
cd opencode-unlimited-2.3.1
sudo bash tests/run_tests.sh                 # offline + real (needs WARP)
bash tests/run_tests.sh --offline            # 23 tests, no WARP, no network changes
bash tests/run_tests.sh --select=t31         # one test
TEST_TIMEOUT=600 bash tests/run_tests.sh     # slower machines
```

### Real end-to-end test with opencode itself — added in 2.0.1

The suite below uses fakes for the offline half and a real WARP tunnel for the
real half. This section is different: it is a **real opencode 1.19.5 client
hitting the real free-tier limit**, with the tool installed from the shipped
archive. Setup: Debian 13, `warp-cli` 2026.8.2115.0, model
`opencode/flint-40b-free`, driver `tests/real-limit-test.sh`.

**Timeline, taken from opencode's log and `supervisor.log` (not from
wall-clock guesses):**

| Time (UTC) | Event | Source |
|---|---|---|
| 12:30:53.721 | opencode logs `AI_APICallError: Free usage limit reached…` | `~/.local/share/opencode/log/opencode.log` |
| 12:30:55 | `quota wall detected` — **2 s later** | `supervisor.log` |
| 12:30:56 | `kill-switch lifted` (real IP visible from here) | `supervisor.log` |
| 12:31:01 | `waiting 9s for the old registration to clear` | `supervisor.log` |
| 12:31:10 | `registration new: ok (attempt 1, cycle 1)` | `supervisor.log` |
| 12:31:12 | `kill-switch re-installed` → `rotation done in 16.76s` | `supervisor.log` |
| — | `/64` changed `2a09:bac5:b043:1caa` → `2a09:bac5:b043:119` | `api64.ipify.org` |

**19 seconds from the wall line to a fresh, working block, with no human
involvement.** Detection was automatic, the cooldown was respected, the guard
was lifted and restored, and the new block was not one we had already used.

A second real wall, 4 minutes later, exercised the spent-block logic:

```
12:25:05 WARN  quota wall detected (Free usage limit reached)
12:25:24 WARN  Cloudflare handed back a /64 we already exhausted (2a09:bac1:76c0:dba8) — asking for another one
12:25:41 INFO  rotation done in 33.61s: 2a09:bac5:b043:1caa -> 2a09:bac1:7680:dba8
```

And the honest end state after ~20 rotations in one day, when Cloudflare has no
unspent `/64` left for the device:

```
12:41:16 WARN  Cloudflare handed back a /64 we already exhausted (2a09:bac1:7680:dba8) — asking for another one
12:41:33 WARN  Cloudflare handed back a /64 we already exhausted (2a09:bac1:7680:dba8) — keeping it; the free quota
               for that block is already spent, so you may still be rate-limited until Cloudflare's window resets
12:41:33 INFO  rotation done in 33.59s: 2a09:bac5:b045:1541 -> 2a09:bac1:7680:dba8
```

The tunnel stays up, the internet keeps working, and the tool says plainly that
it could not find fresh quota instead of reporting a success it did not achieve.

Full narrative, verdict and limitations: **`REAL-OPENCODE-TEST.md`**.

### The shipped archive was re-verified after extraction

The ZIP was unpacked into an empty directory and both suites were run from
there — nothing from the build tree, no leftover install:

```
/tmp/zipcheck/opencode-unlimited-2.0.1$ sudo bash tests/run_tests.sh --offline
  results: 22 passed  0 failed  0 skipped

/tmp/zipcheck/opencode-unlimited-2.0.1$ sudo bash tests/run_tests.sh --select=t3
  PASS t30_real_install      rotation 18s: 2a09:bac5:b045:3705 -> 2a09:bac1:76c0:dba8
  PASS t31_real_auto_rotation
  PASS t32_real_no_deadlock  internet back after 39s
  PASS t33_real_hop          2a09:bac1:7680:dba8 -> 2a09:bac5:b041:1541
  PASS t34_real_uninstall
  results: 5 passed  0 failed  0 skipped
```

The offline suite never touches the real network: `warp-cli`, `iptables`,
the egress probe and `opencode` are replaced by deterministic fakes in
`tests/helpers/`, each driven by a JSON state file so a test can make
`registration new` fail twice and then succeed, or make the tunnel stay dead.

---

## 1. Requirement-by-requirement verdict

### R1 — Detect `Free usage limit reached`; idempotent; cooldown; never blacklist an empty /64

| Test | What it actually did | Result | Evidence |
|---|---|---|---|
| t03 | Wrote the verbatim opencode 1.19 wall text into a fake log, ran `ouc scan-once` | PASS — detected, `pending_wall=true` | `logs/03.raw` |
| t05 | Same with the legacy `QuotaExceeded` wording | PASS — still detected | `logs/runner.t05.log` |
| t04 | Ran the scan over identical bytes twice; then truncated the log **to the same byte length** and re-scanned | PASS — second scan reports `no new bytes`; the rewritten log is re-read (offset reset via the `tail_sig` hash, not just file size) | `logs/runner.t04.log` |
| t06 | Forced a wall, then a second wall inside the cooldown; then let the cooldown expire | PASS — second rotation refused with `skipped: cooldown`, the pending wall survived and was served afterwards | `logs/06.raw` |
| t07 | Rotation where the old egress could not be probed (`""`) | PASS — `blacklist after a rotation with unknown old block: []` | `logs/07.raw` |

Detection is a byte-offset cursor, not a grep of the whole file: the state
keeps `log_id` (`dev:inode`), `offset` and `tail_sig` (SHA-256 of the 64 bytes
before the offset). Same bytes can never fire twice; a new, shrunk or
silently rewritten log is re-read from 0. On the first pass after a
(re)start the supervisor *baselines* at the current end of the log, so
yesterday's wall does not trigger a rotation on boot.

### R2 — Wait ≥ 8–10 s between `registration delete` and `registration new`; bounded retries; no broken end state; full rotation < 30 s

| Test | What it actually did | Result | Evidence |
|---|---|---|---|
| t08 | Fake `warp-cli` records the timestamps it is called with; asserts the gap | PASS — `rotation took 11.16s`, `warp-cli rejections caused by racing the delete: 0` | `logs/08.raw` |
| t09 | Fake fails `registration new` twice with the real API error, then succeeds | PASS — `failed registration attempts before success: 2`, backoff 3 s → 6 s, then success | `logs/09.raw` |
| t31 | **Real** Cloudflare: fabricated wall in the live opencode log, real rotation | PASS — `2a09:bac1:7680:dba8 -> 2a09:bac5:b043:1d37` in **20 s** | `logs/31.raw` |
| t33 | **Real** `ouc hop` from an unprivileged user | PASS — `2a09:bac1:76a0:dba8 -> 2a09:bac5:b042:3705`, exit 0, one attempt | `logs/33.raw` |

Order of a rotation, as seen in the real log `logs/31.raw`:

```
rotation start (quota-wall); current egress=2a09:bac5:b044:3705::57b:2f
kill-switch lifted (traffic goes out on the real interface)
waiting 9s for the old registration to clear
registration new: ok (attempt 1, cycle 1)
kill-switch re-installed
```

Cloudflare sometimes hands the **same** /64 straight back. That is not an
error the user can fix, so `rotate()` repeats the whole
delete → settle → register → connect cycle (bounded by
`OUC_SAME_BLOCK_RETRIES`, default 2) before it reports failure. Bounded
retries, `reg_backoff * 2**(n-1)` backoff, and every failure path closes with
a firewall decision (see R3), so there is no half-rotated end state.

### R3 — The kill-switch must never cut the internet

| Test | What it actually did | Result | Evidence |
|---|---|---|---|
| t10 | Made `registration new` fail permanently with the guard installed | PASS — the `OUC_LEAKGUARD` rules were removed, internet reachable, `recorded error: registration failed: Error: Failed to communicate with the WARP API` | `logs/10.raw` |
| t11 | Normal rotation | PASS — guard re-installed on success | `logs/11.raw` |
| t12 | `OUC_DISABLE_KILL_SWITCH=1` | PASS — no rules at all, rotation still works | `logs/12.raw` |
| t15 | Supervisor with the tunnel already dead | PASS — `tunnel back after 14s; guard restored after +2s (True)` | `logs/15.raw` |
| t32 | **Real** `warp-cli disconnect` + `registration delete` while the guard was armed | PASS — `internet back after 38s`, egress an IPv6 address, guard rules restored | `logs/32.raw` |
| t34 | **Real** uninstall | PASS — still online over IPv6, no rules left behind | `logs/34.raw` |

Design: the rules are **lifted first** in every code path that can end with
no tunnel (rotation start, repair, startup with a dead tunnel, `stop`,
process exit via `ExecStopPost`, uninstall). If the tunnel does not come
back, the supervisor stays lifted, sets `degraded=true` and logs a
`SECURITY` line saying the real IP is visible — it never leaves the machine
firewalled with no way out. `sudo ouc rescue` is the last-resort path and
works even with a corrupt state file.

### R4 — No advertising, no signatures, no output mangling, no `AGENTS.md`

| Test | What it actually did | Result | Evidence |
|---|---|---|---|
| t01 | Built the v1 ad string at runtime, scanned every shipped file for it, grepped for code that writes an agent-rules file, then ran the wrapper | PASS — `shipped files containing an ad signature: none`, `code that writes an agent-rules file: none` | `logs/runner.t01.log` |
| t02 | Ran the same prompt through the real binary and through the wrapper, compared SHA-256 | PASS — byte-identical, including a deliberately missing trailing newline and colour codes | `logs/runner.t02.log` |
| t21 | Uninstall on a box carrying v1 leftovers | PASS — `/etc/sudoers.d/opencode-unlimited`, the user unit, the keeper, `~/.opencode/AGENTS.md` all gone | `logs/21.raw` |

```
direct: 66616b652d6f70656e636f6465206172672d636f756e743d320a414e53493a201b5b313b33316d7265641b5b306d0a6e6f2d747261696c696e672d6e65776c696e652d6d61726b6572
lane  : 66616b652d6f70656e636f6465206172672d636f756e743d320a414e53493a201b5b313b33316d7265641b5b306d0a6e6f2d747261696c696e672d6e65776c696e652d6d61726b6572
```

The wrapper `opencode-warp` only ever `exec`s the real binary. Nothing is
prepended, appended, filtered or rewritten — byte equality is asserted, not
assumed. The single base64 token `RWxlY3Ryb25DdXJzZWQ=` in the tree is a
*deletion* marker: it is decoded at runtime to recognise and remove the file
the old version planted.

### R5 — Work with no systemd user session (systemd **or** portable)

| Test | What it actually did | Result | Evidence |
|---|---|---|---|
| t13 | `ouc start` / `status` / `stop` with no systemd at all | PASS — one supervisor, PID file honoured, `pid before/after second start: 30309 / 30309` | `logs/13.raw`, `logs/13.status` |
| t18 | Full install with `systemd --user` unusable | PASS — `confirmed: systemd --user is not usable here`, portable mode, supervisor alive | `logs/18.raw` |
| t19 | Root unit install | PASS — unit file valid, `ExecStopPost` removes the rules even on a crash | `logs/19.raw` |
| t30 | **Real** clean install on the live host | PASS — `mode: systemd`, `supervisor: running (pid 31453)`, `warp tunnel: Connected`, `egress: 2a09:bac5:b044:3705::57b:2f`, `kill-switch: installed` | `logs/30.raw`, `logs/30.status` |
| t22 | `ouc doctor` | PASS — coherent report; exit 1 only because the sandbox had no tunnel at that moment | `logs/22.raw` |

`ouc status` and `ouc hop` share one liveness helper, so a healthy systemd
install is never misreported as "portable / not running", and a user with no
root can still ask the root supervisor for a hop. Missing `/dev/net/tun` or
iptables is reported as a clear, actionable message instead of a traceback,
and the hop is offered manually.

### R6 — Sudoers restricted

The v1 drop-in `/etc/sudoers.d/opencode-unlimited` granted a blanket
`NOPASSWD` rule for a user-writable script. v2 installs **no** sudoers file
at all: privileged work happens in a root-owned systemd unit, and the
unprivileged user talks to it through a request/result file
(`~/.local/state/opencode-unlimited/hop.request`, or `/tmp/opencode-unlimited/`
when `~/.local` is not writable). Uninstall deletes the v1 drop-in — verified
in t21. `sudo ouc status` also runs with no sudoers entry because the tools
are world-executable and read-only state is `0644`.

### R2b — behaviour under real opencode traffic (observed, not simulated)

| What happened | Result |
|---|---|
| Wall written by opencode itself | detected in **2 s** |
| Rotation, start to fresh working `/64` | **19 s** (17 s + detection) |
| Rotation that had to reject a spent `/64` | 33.6 s (2 cycles) |
| Guard restored after each rotation | yes, +2 s after connect |
| `/64` actually changed | yes — verified against `api64.ipify.org` |
| After the pool ran dry | warning logged, tunnel kept up, internet fine |
| Rotations in one busy day | 20, all logged, 10 old `/64`s blacklisted |

### R7 — Real tests with logs

This file. 23 offline + 5 real tests, all executed, all logs kept — plus the
real opencode limit test described above, run separately on 2026-11-08.

### R8 — Package contents

`README.md`, `INSTALL.md`, `UNINSTALL.md`, `SECURITY.md`,
`TEST_RESULTS.md`, `CHANGELOG.md`, `install.sh`, `uninstall.sh`, `ouc.py`
(`doctor`, `hop`, `rescue`, supervisor), `opencode-warp`, `tests/`, and
`tests/logs/` with the real logs of this run.

---

## 2. Full test table

### Offline (deterministic fakes, safe to run anywhere — 23 tests)

| # | Test | Assertion proved | Log |
|---|---|---|---|
| 1 | `t01_no_advertising_in_tree` | no ad text, no signature, nothing writes an agent-rules file | `runner.t01.log` |
| 2 | `t02_lane_is_transparent` | wrapper output is byte-identical to the real binary | `runner.t02.log` |
| 3 | `t03_detect_real_message` | `Free usage limit reached` is detected | `03.raw` |
| 4 | `t04_detection_is_idempotent` | same bytes fire once; a rewritten log is re-read | `runner.t04.log` |
| 5 | `t05_detects_legacy_message` | `QuotaExceeded` still detected | `runner.t05.log` |
| 6 | `t06_cooldown_and_pending` | cooldown blocks a second rotation; pending survives | `06.raw` |
| 7 | `t07_blacklist_never_empty` | an unknown/empty egress is never blacklisted | `07.raw` |
| 8 | `t08_rotation_respects_settle` | ≥ 8 s settle, whole rotation < 30 s | `08.raw` |
| 9 | `t09_retry_with_backoff` | 2 failed registrations retried with backoff, then success | `09.raw` |
| 10 | `t10_failure_keeps_internet` | a failed rotation leaves no firewall and no deadlock | `10.raw` |
| 11 | `t11_firewall_restored_on_success` | guard re-installed after a good rotation | `11.raw` |
| 12 | `t12_disable_killswitch_env` | `OUC_DISABLE_KILL_SWITCH=1` really disables it | `12.raw` |
| 13 | `t13_portable_lifecycle` | start/status/stop, single instance, PID file stable | `13.raw` |
| 14 | `t14_supervisor_does_the_rotation` | wall at the end of the pipeline → supervisor rotates by itself | `14.raw` |
| 15 | `t15_supervisor_repairs_dead_tunnel` | dead tunnel repaired in 14 s, guard re-armed 2 s later, no deadlock | `15.raw` |
| 16 | `t16_log_rotation` | logs rotate instead of growing forever | `16.raw` |
| 17 | `t17_state_corruption_recovery` | `state.json` full of garbage → recovered, still valid JSON | `runner.t17.log` |
| 18 | `t18_install_portable` | install with no systemd user session | `18.raw` |
| 19 | `t19_install_systemd_unit` | root unit written correctly | `19.raw` |
| 20 | `t20_install_idempotent` | second install keeps the same supervisor PID (`31055 / 31055`) | `20.raw` |
| 21 | `t21_uninstall_complete` | everything removed, including v1 leftovers | `21.raw` |
| 22 | `t22_doctor` | coherent health report | `22.raw` |
| 23 | `t23_blacklist_skips_spent_block` | a `/64` already burned today is rejected and another one requested | `23.raw` |

### Real (this machine, live Cloudflare WARP — 5 tests)

| # | Test | Measured | Log |
|---|---|---|---|
| 30 | `t30_real_install` | clean install → supervisor running, tunnel connected, guard armed | `30.raw`, `30.status` |
| 31 | `t31_real_auto_rotation` | wall detected → `2a09:bac1:7680:dba8` → `2a09:bac5:b043:1d37` in **20 s** | `31.raw` |
| 32 | `t32_real_no_deadlock` | tunnel destroyed under an armed guard → internet back in **38 s** | `32.raw` |
| 33 | `t33_real_hop` | unprivileged `ouc hop` → `2a09:bac1:76a0:dba8` → `2a09:bac5:b042:3705` | `33.raw` |
| 34 | `t34_real_uninstall` | uninstall → still online over IPv6, no rules left | `34.raw` |

---

## 3. Bugs found by these tests (and fixed)

These were all caught by running the suite, not by reading it:

1. **Guard deadlock** — the rotation deleted the registration while the
   firewall blocked the WARP control API, so it could never come back. Fixed
   by lifting the rules before any teardown.
2. **`StateLock` self-deadlock** — a nested `with` on the same lock blocked
   forever; the registry is now depth-counted.
3. **Pending wall lost during cooldown** — detection inside the cooldown
   window was never served after it expired. `scan-once` now always consults
   the pending state.
4. **`grep -c … || echo 0`** in the runner produced `0\n0` and broke a
   counter assertion.
5. **Corrupt state file** was fatal instead of recoverable.
6. **Truncate-to-same-length** defeated the offset check → added `tail_sig`.
7. **Ad literal hidden in `install.sh`** (a `grep` for a v1 marker) tripped
   the ad scan → replaced with a runtime-decoded marker.
8. **Leaked supervisors** between tests inflated the process count → the
   runner now `pkill`s its own leftovers and asserts the PID file.
9. **`mode` was never written to `config.json`** by `install.sh`, so a
   perfectly healthy systemd install reported `mode: portable`,
   `alive: false`, and `ouc hop` refused to run. Fixed by recording the mode
   at install time and by auto-detecting it from the installed unit;
   `status`, `hop` and `doctor` now share one liveness helper
   (`systemd unit active` **or** a live PID file).
10. **Startup rotated on a historic wall** — the first scan read the log from
    byte 0 and found yesterday's quota message, so a fresh install
    disconnected the tunnel during its own verification. The first scan now
    baselines at the current end of the log.
11. **Cloudflare returns the same /64** after a fresh registration → bounded
    retry of the whole cycle instead of a hard failure.
12. **`ouc hop` as a normal user** failed when `~/.local` was root-owned →
    added a fallback request channel under `/tmp/opencode-unlimited/`.
13. **`ouc status` lied to normal users** (found live): without root, the
    `iptables -C` query fails, so a healthy, armed kill-switch was reported as
    "NOT installed". The supervisor now records `guard_installed` in
    `state.json`; an unprivileged `status` reports that and says so, while
    `sudo ouc status` still asks the kernel.
14. **The blacklist silently dropped its own entries** (found live): blocks were
    stored as bare `/64` strings but re-validated through `norm64()`, which
    expected a full address — so every entry except the newest was discarded on
    each rotation. `norm64()` now accepts both forms.
15. **The blacklist was never consulted** (found live): rotation accepted any
    block that merely differed from the current one, including blocks already
    exhausted that day, so a "successful" rotation could leave you limited one
    request later. Spent blocks (24 h TTL) are now rejected and retried, with an
    honest warning when nothing fresh is available. New test `t23`.

---

## 4. Honest limitations

- **Cloudflare recycles `/64`s.** The free quota is per block per window, so on
  a heavy day it hands back blocks whose quota is already spent. The tool now
  rejects the ones it remembers (`t23`) and warns when it runs out of better
  options — but no tool can conjure quota that Cloudflare is not giving out.
- **opencode itself retries before it gives up.** In this run a limited
  `opencode run` took 88–300 s to return even though the wall was logged within
  2 s. The in-flight request still fails; the *next* one goes out on the new
  block. That delay is opencode's, not ours.
- The real tests need a machine where WARP can actually connect (IPv6
  egress). In a container without `/dev/net/tun` they report `SKIP`, which is
  why the runner keeps the offline suite separate and dependency-free.
- Cloudflare's per-/64 quota (~48–80 requests per window) is measured
  behaviour, not a documented contract. Detection never depends on that
  number: it only reacts to the wall line.
- `t22` exits 1 when no tunnel exists; the test treats that as a correct
  report, not a failure.
- While a rotation is in progress the real IP is visible — that is
  unavoidable if the machine is to keep its internet. It is logged as a
  `SECURITY` line and documented in `SECURITY.md`.

---

# 2.1.0 — what the real measurements changed

Same machine, same day, same live WARP tunnel and the same opencode 1.19.5.
The numbers below are from `tests/logs/31.raw`, `32.raw`, `33.raw` and
`/var/lib/opencode-unlimited/supervisor.log`.

## Result

```
28 passed, 0 failed, 0 skipped     (offline suite, now including t24–t28)
 5 passed, 0 failed, 0 skipped     (live WARP: t30–t34)
```

## Finding 1 — rotating /64s was doing nothing for IPv4 traffic

`ss -tnp` during an ordinary request showed opencode dialling
`172.16.0.2 → 104.20.32.17:443` and **only** that; the tunnel's IPv6 address
was never used. WARP's IPv4 egress is a shared pool of five addresses, so a new
`/64` changed the address the backend saw not at all.

After installing the pin (managed block in `/etc/hosts` for `opencode.ai` and
`api.opencode.ai`):

```
before:  1 × 172.16.0.2 → 104.20.32.17:443   + an IPv6 socket   (probe: LIMITED)
after :  1 × [2606:4700:10::6814:2011]:443      only            (probe: OK)
```

A pin with no IPv6 path behind it breaks the site outright (`curl: (7)`) — which
is why installation is conditional on a resolved AAAA record and the supervisor
removes the pin whenever the tunnel drops.

## Finding 2 — rotate-keys is 5× faster than re-registration

| Path | Wall → usable, measured |
|---|---|
| `warp-cli tunnel rotate-keys` + real verification | **7.0 s** (auto), **7.06 s** (manual hop, first attempt), 7.84 s, 8.21 s, 9.66 s |
| `registration delete` + `new` (v2.0.1 behaviour) | 17–25 s |

## Finding 3 — a rate-limited block hangs, it does not error

Verification used to be a blocking `opencode run` with a 25 s timeout. On a
burned block opencode streams nothing and never exits; two attempts cost **53 s**
and still produced no answer. Verification now polls the opencode log while the
request runs and kills it the moment a quota line appears, and (when the
supervisor is root) runs the request as the target user, because `opencode run`
with root's `$HOME` hangs indefinitely.

## Finding 4 — recycled blocks are the normal case

`2a09:bac1:7680:dba8` — burned during the first proof-of-concept run — was
handed back later the same afternoon, still limited. An address change proves
nothing; only a request does. Blocks therefore carry timestamps in
`state.json`, expire after **4 h** (measured window: a block burned at 12:05 was
usable again at 16:15, one burned at 15:53 was still limited at 16:10), and are
skipped while they are hot.

## What the rotation does now

1. install/refresh the IPv6 pin
2. lift the kill-switch (the repair window stays open, as documented)
3. `warp-cli tunnel rotate-keys` → wait for a new IPv6 egress
4. skip it if it is the same block or a recently spent one
5. **make one real request** (`ouc verify` logic); a wall or a hang means
   "try again" (up to `OUC_KEYS_ATTEMPTS`)
6. if no candidate can be confirmed: full re-registration (delete → settle →
   new), same verification
7. if that fails too: stay online, record the error, tell the user to run
   `ouc hop` — never a dead end
