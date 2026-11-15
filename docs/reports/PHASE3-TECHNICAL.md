# Phase 3 — Psiphon as an automatic fallback backend
## Technical report

**Subject:** `opencode-unlimited` v2.5.1 — WARP-primary / Psiphon-fallback
escalation ladder
**Commit under test:** `601f89a` (`main`), tree clean
**Host:** disposable GitHub Codespace `shiny-umbrella-jr7959p9w5v5f5qqp`
(standardLinux32gb, WestUs2, Ubuntu, provisioned 14:15Z, deleted 18:12Z)
**Window:** 2026-11-14 14:16Z → 18:12Z
**Evidence:** `/home/user/lab3/evidence/` (snapshots, per-test JSON, supervisor
log, audit, `git-verify.json`)

---

## 1. Result summary

| Item | Result |
| --- | --- |
| Offline suite (`tests/run_tests.sh --offline`) | **65 passed / 0 failed / 11 skipped** |
| pytest (`test_runner_v2.py`, `test_transport_resilience.py`) | **100 passed**, 11 subtests |
| Test 1 — forced WARP exhaustion → automatic Psiphon | **PASS**, escalation on attempt 5/5 |
| Test 2 — hands-free recovery across ≥3 Psiphon regions | **PASS**, DE → NL → CA, 3 distinct `/32`, 3/3 token recall |
| Test 3 — 18-check before/after audit | **18/18 PASS** |
| Codespace lifecycle | created, used, deleted, `GET` returns **404** |

Five defects were found on the live host that the offline suite could not see.
All are fixed, each has a regression test, and the fix commit is the commit that
ran the tests (`git-verify.json`: `identical: true`).

---

## 2. The five live-host defects

### 2.1 The `Tunnels` notice was never matched

`Psiphon.start()` spawns:

```
psiphon-tunnel-core -config <state>/psiphon/psiphon.config -formatNotices
```

`-formatNotices` switches stdout from JSON to the console dialect. Measured on
the host:

```
2026-11-14T17:55:43.156Z Tunnels {"count":1}
```

The matcher was `re.compile(r'"noticeType"\s*:\s*"Tunnels"')` — the *default*
JSON shape, which this invocation never emits. A fully established tunnel was
therefore reported as:

```
✗ rotation failed: no working Psiphon exit right now
  [psiphon@US=- (no tunnel within 45s: …inproxy…), psiphon@DE=- (…)]
```

while the process had in fact connected 12 s in. Every escalation then fell back
to WARP, which is why the first live escalation test showed `streak=7` with
`backend=warp`.

**Fix.** Both dialects are accepted:

```python
TUNNEL_RE = re.compile(r'(?:"noticeType"\s*:\s*"Tunnels"'
                       r'|(?:^|\s)Tunnels\s*\{)')
```

`count:0` (shutdown) is still rejected by `COUNT_RE`.

**Why the suite missed it.** `tests/helpers/fake_psiphon.py` emitted the JSON
shape regardless of how it was invoked, so the fake and the matcher shared the
same wrong assumption. The fake now checks `"-formatNotices" in sys.argv` and
emits the console dialect when present — it mirrors the binary, not the code.

### 2.2 A failed escalation still recorded a fallback episode

`rotate()` wrote `fallback_activated_at = now()` *before* attempting the Psiphon
rotation. If Psiphon could not produce an exit, the timestamp stayed behind and
`status`/`choose_backend()` would then believe a fallback was in progress. The
write was removed; `_rotate_psiphon()` sets it itself on success.

### 2.3 A wall arriving during a tunnel repair was discarded

`Supervisor.tick()` order was:

```
ensure_tunnel()      # can block ~60 s repairing
sync_address()
enforce_firewall()
scan_wall(baseline=first)
```

Observed: supervisor started 14:39:31, spent 14:39:31→14:40:31 repairing, and
the first scan ran *after* the wall had been written. Because that first scan
ran with `baseline=True`, `scan_wall` treated the bytes as pre-existing history,
advanced the offset, and returned `no new bytes` on every later tick. The wall
was invisible to the supervisor for the whole run (`last_wall=0`,
`pending_wall=False`, egress stuck on a walled block).

**Fix.** Two changes:

* `scan_wall()` runs *before* the tunnel work, so the wall is recorded even if
  the repair takes a minute; rotation stays after `ensure_tunnel()` so the two
  recoveries never contend for `rotation.lock`.
* The baseline is taken once in `run_forever()` at process start, not by
  whatever the first full tick happens to see.

Measured after the fix:

```
15:09:05 WARN  quota wall detected (AI_APICallError: Free usage limit reached…)
15:09:06 INFO  rotation start (quota-wall); egress=2a09:bac5:cbbd:1541::21e:16e
15:09:13 INFO  rotation done in 7.37s via rotate-keys#1: … (verified with a real request)
```

### 2.4 The daemon gave its children a write-only stdin

`daemonize()` did:

```python
fd = os.open(cfg.supervisor_log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
os.dup2(fd, 0); os.dup2(fd, 1); os.dup2(fd, 2)
```

So the supervisor's fd 0 was a write-only log file, and `run_child()` passed
`stdin=None` for the non-structured path — "inherit fd 0". `opencode` reads
stdin, so it died instantly with:

```
EBADF: bad file descriptor, read
```

Every rotation performed by the daemon therefore failed verification
(`could not be confirmed`) while the same rotation run from an interactive shell
succeeded in 8–13 s. Confirmed by inspecting the live process:

```
$ ls -l /proc/<supervisor>/fd
l-wx------ 0 -> /var/lib/opencode-unlimited/supervisor.log
```

**Fix.** stdin is `/dev/null` in the daemon, and `run_child()` hands out
`/dev/null` unless fd 0 is actually readable (`F_GETFL & O_ACCMODE`), not merely
open. Diagnose-only change alongside: verify failures now append the child's last
output line (stdout, falling back to stderr) instead of a bare `exit 1`.

### 2.5 The tunnel died with the command that started it

`Psiphon.start()` used `stdout=subprocess.PIPE` and followed it from a thread.
The pipe's read end belonged to the rotating process. A `hop` that reported

```
✓ rotated in 13.73s — resend your message
```

handed over a tunnel that was gone ~50 s later when that process exited; the
bridge then answered `502 Bad Gateway` to every request, and the fallback that
had just been "verified" was not there when the next task needed it.

**Fix.** The tunnel's stdout is `<state>/psiphon/psiphon.log`, opened by the
child; the controller follows the same file from the offset it started at. The
tunnel now belongs to the machine, which is what a daemon needs.

---

## 3. Test 1 — forced WARP exhaustion escalates by itself

Method: `warp-cli disconnect` + `registration delete`, ladder state reset
(`active_backend=warp`, `warp_fail_streak=0`, `fallback_activated_at=0`,
`psiphon_region_index=0`), then repeated `ouc hop` with
`OUC_WARP_FALLBACK_THRESHOLD=5`. No Psiphon command was issued by hand.

```
hop 1: backend=warp    streak=1
hop 2: backend=warp    streak=2
hop 3: backend=warp    streak=3
hop 4: backend=warp    streak=4
hop 5: backend=psiphon streak=5  egress=138.197.197.180
escalated at attempt 5, host direct IP 51.143.4.80
```

* Escalation at exactly the configured threshold, in the same call, no human.
* Region `US`, exit `138.197.197.180`, distinct from the host's own address.
* Blacklist entry written as a normalised `/32` (not `/64`): the same dict now
  holds both families.

## 4. Test 2 — hands-free recovery across three Psiphon regions

Method: dynamic bridge on `127.0.0.1:8080` (the single address opencode keeps),
fresh exit, seed one conversation, then three rounds of
`ouc hop --backend psiphon` + `ouc resume -s <session>`.

Seed block `172.238.125.84`, token `TOKEN-FALLBACK-78851`,
session `ses_e24cdaec8ffewU2vmhygUtywfc`.

| Round | Region | Block | Rotation + verify | Continuity (`resume`) |
| --- | --- | --- | --- | --- |
| 1 | DE | `212.227.185.145/32` | yes (real request) | token recalled, 15.97 s |
| 2 | NL | `206.189.15.19/32` | yes | token recalled, 16.43 s |
| 3 | CA | `159.203.56.122/32` | yes | token recalled, 15.26 s |

* 3 distinct regions, 3 distinct `/32` blocks, `all_verified: true`,
  `all_recalled: true`, same session throughout.
* `HTTPS_PROXY` never changed; the bridge followed `active_socks_port`
  40000 → 9060 on its own.
* The tunnel log for round 3 confirms a real tunnel-core connection:
  `ConnectedServer … "protocol":"INPROXY-WEBRTC-OSSH","region":"CA"`,
  `"isReplay":false` (so `DisableReplay: true` is doing its job),
  then `Tunnels {"count":1}`.

### Quota behaviour observed (not a product defect)

The free tier is metered **per block**, with a small allowance. During earlier
iterations every round made 3–4 model calls (rotation verify + standalone verify
+ resume, plus retried seeds), which exhausted each fresh block mid-round and
produced `verification hit the quota wall` and `all 4 free models walled` from a
single WARP block. The product behaved correctly throughout — it *refused* the
walled exits and moved on. The harness was corrected to seed on a fresh exit and
make one call per round; the run above is the result.

## 5. Test 3 — 18-check before/after audit

`/home/user/lab3/audit.py`, computed only from `state-pre.json` (14:16Z) and
`state-post.json` (18:04Z) plus the per-test JSON. No values are typed in.

```
 1 the machine still has its real internet             PASS  direct IP 51.143.4.80 before/after
 2 /etc/hosts byte-identical                           PASS  sha256 3fb626a1…
 3 /etc/resolv.conf byte-identical                     PASS  sha256 e766628e…
 4 IPv4 routing table unchanged                        PASS  6 -> 6
 5 IPv6 routing table unchanged                        PASS  3 -> 3
 6 IPv4 policy rules unchanged                         PASS  4 -> 4
 7 IPv6 policy rules unchanged                         PASS  3 -> 3
 8 IPv4 firewall unchanged (counters normalised)       PASS  47 -> 47
 9 IPv6 firewall unchanged                             PASS  25 -> 25
10 no psiphon tunnel left running                      PASS  0 real processes
11 fallback SOCKS port closed                          PASS  :9060 False
12 HTTP bridge stopped                                 PASS  :8080 False
13 tested controller == pushed commit                  PASS  sha256 63a94a69… @ 601f89a
14 no local-only edits left                            PASS  git status clean
15 escalation on the 5th failed WARP rotation          PASS  attempt 5/5
16 fallback exit is never the host's own address       PASS  138.197.197.180 ≠ 51.143.4.80
17 wall rotated with no human involved                 PASS  hops 3 -> 4
18 three regions carry the same conversation           PASS  CA/DE/NL, 3 blocks
18/18
```

Note on check 10: `pgrep -af psiphon-tunnel-core` matches its own shell command
line. The audit filters that out and reports the real process count (0),
cross-checked with `ps -eo pid,args` and `ss -lnt`.

## 6. Harness artefacts encountered (not product bugs)

Recorded so they are not mistaken for findings later:

* `ouc stop --portable` is not a subcommand — the driver's stop silently failed
  and a stale supervisor held `rotation.lock`, so manual `hop` returned
  "rotation already in progress". Fixed in the driver (`ouc stop`).
* A hand-started static bridge (`--socks 127.0.0.1:40000`) shadowed the dynamic
  one and pinned traffic to WARP while Psiphon was "active", producing one
  misleading round where the block appeared unchanged. The dynamic bridge is now
  the only one started.
* Two driver instances raced each other, interleaving evidence files. Runs are
  now serialised.
* `pgrep -af <pattern>` inside a shell wrapper self-matches (see check 10).

## 7. State of the tree

```
ccc0c7e docs: CHANGELOG + README for 2.5.1 (the five live-host defects)
601f89a v2.5.1a: let the tunnel outlive the command that started it
3090f8b v2.5.1: make the Psiphon fallback actually reachable on a live host
34e6e9b v2.5.0: Psiphon as an automatic fallback backend
```

Design constraints held: single file `ouc.py`, Python 3.8+ stdlib only, zero pip
dependencies, `flock`-based ownership, no dotfile or advertising-file pollution.

## 8. What was not proven

* **Hands-free wall recovery while traffic is carried by Psiphon.** Test 2a
  exercised the wall path end-to-end on WARP (7.37 s, verified). The Psiphon leg
  was exercised through `--backend psiphon`, which is the same code path
  `choose_backend()` selects after a wall, but not a wall-triggered selection.
  A quota wall that lands *while* a Psiphon block is carrying traffic will
  rotate within Psiphon, but that specific trigger was not observed live.
* **A 10-hour soak.** Longest continuous run in this phase was ~4 h of wall
  clock with 20 hops and 17 Psiphon rotations.
* **Tunnel-mode kill-switch interaction with Psiphon.** All live tests ran in
  proxy mode (`OUC_WARP_MODE=proxy`), where no firewall is managed. Raising the
  guard while a Psiphon exit is carrying traffic is untested.
