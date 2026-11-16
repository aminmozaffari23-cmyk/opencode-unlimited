<div align="center">

# opencode-unlimited

**A controller that keeps `opencode` working when the free tier says stop.**

Rotate a Cloudflare WARP `/64`, and when that pool is exhausted, hand the same
session to Psiphon — automatically, verified, and without the conversation
starting over.

[![Version](https://img.shields.io/badge/version-2.6.5-1f6feb?style=flat-square)](CHANGELOG.md)
[![Offline suite](https://img.shields.io/badge/offline%20suite-87%20passed%20%7C%200%20failed-2ea043?style=flat-square)](tests/run_tests.sh)
[![pytest](https://img.shields.io/badge/pytest-116%20passed-2ea043?style=flat-square)](tests/test_runner_v2.py)
[![Backends](https://img.shields.io/badge/backends-WARP%20%2B%20Psiphon%20fallback-8957e5?style=flat-square)](#how-the-dual-engine-works)
[![Dependencies](https://img.shields.io/badge/dependencies-Python%203.8%2B%20stdlib%20only%20%C2%B7%20zero%20pip-0b7285?style=flat-square)](#requirements)
[![Environment](https://img.shields.io/badge/environment-Linux%20VM%20%C2%B7%20GitHub%20Codespaces%20%C2%B7%20Docker%20(see%20note)-57606a?style=flat-square)](#docker)

</div>

**Interactive TUI:** a disposable [ping-pong run](tests/lab3/evidence/43814088627/pingpong_live.json)
observed one OpenCode process recall a token across WARP→Psiphon→WARP,
with kernel socket correlation, bridge byte counters and epoch drains. The
four-turn PTY test uses an offline stand-in, not real OpenCode. See
[test results](TEST_RESULTS.md) for the evidence and limits.
Bridge telemetry records destination metadata and currently requires manual
log rotation; see the privacy note there.

---

> **What this is.** Free-tier model access is metered per source address. This
> controller rotates the address underneath a live `opencode` session and proves
> each move with a real request, so a quota wall costs you seconds instead of
> your conversation. It is a queue-jumping tool, not a security product: read
> [Honest limitations](#honest-limitations) before trusting it with anything.

> **What is measured, and what is not.** Everything below marked *measured*
> comes from a command that ran on a disposable root machine, with the
> transcript in this repository's evidence files. Everything marked *offline*
> is reproducible here with fakes. Where a claim still needs a live run, it
> says so — this README does not get to be more confident than its evidence.

## Table of contents

- [What it does](#what-it-does)
- [Requirements](#requirements)
- [Quickstart](#quickstart)
  - [Proxy mode (Codespaces, containers, unprivileged)](#proxy-mode-codespaces-containers-unprivileged)
  - [Tunnel mode (a real Linux VM)](#tunnel-mode-a-real-linux-vm)
- [How the dual-engine works](#how-the-dual-engine-works)
- [The HTTP↔SOCKS5 bridge](#the-httpsocks5-bridge)
- [The leak guard](#the-leak-guard)
- [Chaos & resilience verification](#chaos--resilience-verification)
- [CLI reference](#cli-reference)
- [Configuration](#configuration)
- [Docker](#docker)
- [Honest limitations](#honest-limitations)
- [Evidence](#evidence)
- [Release notes](#release-notes)

## What it does

`opencode`'s free models answer while the source address has allowance. When it
runs out, the client prints a wall and stops. `ouc` (the controller in
`ouc.py`) sits beside it and does five things:

1. **Watches** opencode's own log for the wall — incrementally, idempotently, and
   without ever mistaking an INFO line for an error.
2. **Rotates** the address the provider sees: a new WARP `/64` first, then a
   Psiphon `/32` from another country when WARP's pool is spent.
3. **Verifies** every move with a genuine model request. A changed IP is not
   evidence; a completed reply is.
4. **Blacklists** the address it just burned, so the ladder never hands you the
   same wall twice.
5. **Keeps the session** — the same `opencode` session id, resumed on the new
   address, behind one dial-out address that never changes.

Everything is one file of Python standard library. No pip installs, no daemon
framework, no telemetry.

## Requirements

| | |
|---|---|
| **Runtime** | Python **3.8+**, standard library only |
| **Optional binaries** | `warp-cli` (Cloudflare WARP), `psiphon-tunnel-core`, `opencode` |
| **Privileges** | `root` for tunnel mode (iptables, TUN) · none for proxy mode |
| **Platform** | Linux. Verified on Ubuntu 22.04/24.04 VMs and GitHub Codespaces |

## Quickstart

```bash
git clone https://github.com/aminmozaffari23-cmyk/opencode-unlimited
cd opencode-unlimited
sudo ./install.sh              # tunnel mode (TUN + leak guard)
sudo ./install.sh --proxy      # proxy mode (no TUN, no firewall)
```

### Proxy mode (Codespaces, containers, unprivileged)

For machines with no TUN device. WARP is put in SOCKS5 mode on `127.0.0.1:40000`;
`ouc bridge` presents it as an HTTP proxy on `127.0.0.1:8080`.

```bash
export HTTPS_PROXY=http://127.0.0.1:8080
ouc hop                        # follow the ladder: WARP → Psiphon
ouc run -- "your message"      # one call; on a wall, rotate and continue
ouc status                     # what is carrying traffic, and what just happened
```

`HTTPS_PROXY` **never changes**. That is the point of the bridge: the backend can
be swapped underneath a running task.

### Tunnel mode (a real Linux VM)

WARP creates a TUN device, `ouc` pins `opencode.ai` to IPv6 so the free-tier
meter sees the tunnel's `/64` rather than a shared IPv4 NAT, and `OUC_LEAKGUARD`
drops any traffic that would escape outside the tunnel.

```bash
sudo ouc start                 # supervisor: watches the log, repairs, rotates
ouc status
sudo ouc hop                   # rotate now
sudo ouc pin on                # pin opencode.ai to the tunnel's IPv6
sudo ouc rescue                # force a working network, whatever the state
```

## How the dual-engine works

```
                        ┌──────────────────────────────────────────┐
                        │  opencode  (one address, all day long)   │
                        │  HTTPS_PROXY=http://127.0.0.1:8080       │
                        └───────────────────┬──────────────────────┘
                                            │  HTTP CONNECT
                        ┌───────────────────▼──────────────────────┐
                        │  ouc bridge  127.0.0.1:8080              │
                        │  upstream re-read every 1 s from         │
                        │  state.json → active_socks_port          │
                        └───────┬───────────────────────┬──────────┘
                                │                       │
              active_socks_port│40000         9060      │
                                │                       │
        ┌───────────────────────▼──────┐   ┌────────────▼─────────────────┐
        │  ENGINE 1 · WARP  (primary)  │   │  ENGINE 2 · PSIPHON (fallback)│
        │  Cloudflare tunnel / SOCKS5  │   │  psiphon-tunnel-core SOCKS5   │
        │  rotates a /64               │   │  rotates a /32, per region    │
        │  1. warp-cli rotate-keys     │   │  1. SIGTERM the daemon        │
        │  2. registration delete+new  │   │  2. rewrite EgressRegion      │
        │                              │   │  3. DisableReplay: true       │
        │  ~4–8 s per rotation         │   │  4. wait for Tunnels count:1  │
        └──────────────┬───────────────┘   └────────────▲─────────────────┘
                       │                                │
                       │  5 consecutive failed WARP     │
                       │  candidates, or warp-cli gone  │
                       └────────────── escalate ────────┘
                                        │
                       ┌────────────────▼─────────────────┐
                       │  ESCALATION LADDER (one owner)   │
                       │  ouc hop · run · resume · agent  │
                       │  repair · supervisor tick        │
                       └────────────────┬─────────────────┘
                                        │
                       ┌────────────────▼─────────────────┐
                       │  VERIFICATION                    │
                       │  a real completion, not a ping   │
                       │  wall → rotate · quota ok → send │
                       └────────────────┬─────────────────┘
                                        │
                       ┌────────────────▼─────────────────┐
                       │  UNIFIED BLACKLIST               │
                       │  /64 and /32 in one TTL dict     │
                       │  never the host's own address    │
                       └──────────────────────────────────┘
```

**Escalation.** Each failed candidate counts toward
`OUC_WARP_FALLBACK_THRESHOLD` (default **5**). At the threshold — or when
`warp-cli` is absent — the controller escalates to Psiphon *inside the same
call*. No human, no second command.

**Return.** `fallback_activated_at` is recorded. Once the blacklist TTL (4 h) has
elapsed, or every configured region has been cycled, the next rotation probes
WARP again. Psiphon is the overflow, not the default.

**Rotation is not a restart.** Psiphon keeps one persistent datastore (so the
next tunnel forms in seconds), always sets `DisableReplay: true` (a replay lands
on the same exit), advances `EgressRegion` round-robin, and waits for the real
`Tunnels {"count":1}` notice. `SIGUSR2` is never used: in the console client it
dumps profiles and moves nothing.

## The HTTP↔SOCKS5 bridge

`opencode` speaks HTTP proxy; both backends speak SOCKS5. The bridge converts and
— crucially — **re-reads `active_socks_port` from `state.json` every second**:

```
backend switch (WARP 40000 → Psiphon 9060)
   └─ opencode keeps using http://127.0.0.1:8080
      └─ the next connection goes out the new engine
```

A mid-task fallback therefore needs no restart, no re-exported environment, and
no lost conversation.

## The leak guard

Two backends leak in two different ways, so `OUC_LEAKGUARD` has two dialects.
The dialect is recorded in `state.json` (`guard_mode`) and printed by
`ouc status`.

| Mode | When | What it does |
| --- | --- | --- |
| `tunnel` | WARP carries traffic | Allows only the TUN interface, loopback, RFC1918, and Cloudflare's endpoints; drops the rest |
| `proxy` | Psiphon carries traffic | Scopes egress **by owner**: the account that runs opencode may reach loopback (the bridge) and DNS, nothing else. Psiphon and the daemon keep working |
| `off` | nobody is carrying traffic | Explicitly lifted, with the reason recorded — never silently |

Why this needed fixing: a TUN-shaped guard `DROP`s everything that is not on the
TUN device, but Psiphon is a **userspace** SOCKS5 process dialling out over the
host's own interface. Re-arming the tunnel rules during a fallback kills its
relays — a deadlock. Conversely, leaving the IPv6 `/etc/hosts` pin in place while
Psiphon (IPv4-only) carries traffic sends opencode down a path with no exit.
Both transitions are handled and covered by `t89`.

## Chaos & resilience verification

Every row is a test in `tests/run_tests.sh` **and** was re-run against the real
binaries on a disposable Codespace.

| # | Attack | Expected behaviour | Offline suite |
| --- | --- | --- | --- |
| 1 | `kill -9` the tunnel and the bridge | next supervisor tick respawns both; traffic flows again | `t84` |
| 2 | Squatter binds the SOCKS or bridge port | refuse, name the owning pid, never route into it | `t85` |
| 3 | Corrupt `state.json`, bogus PIDs, stale locks | self-heal, zero tracebacks, state rebuilt valid | `t83` |
| 4 | Five simultaneous `hop`s | `flock` serialises; exactly one tunnel + one bridge survive | `t86` |
| 5 | Two burned regions in a row | skip them by name, stay inside `OUC_ROTATION_TIMEOUT`, never spin | `t87` |
| 6 | 40 random CLI calls during a rotation | no crash, no starvation, no torn writes | `t88` |
| — | Tunnel mode ⇄ Psiphon guard handover | no deadlock, pin lifted, guard dialect recorded, `rescue` restores baseline | `t89` |
| — | A rotation holds the lock while the daemon wants to resume | the daemon's child waits it out; a foreground command still answers instantly | `t90` |
| — | Every Psiphon region is out of quota at once | failure reported, loop bounded, tunnel stopped, backend state honest | `t91` |
| — | Psiphon's first launch is still fetching its server list | one retry of the same region with a longer budget; regions are not burned | `t92` |
| — | A killed tunnel leaves a stale pidfile | `ouc stop` still finds and reaps it | `t93` |
| — | The daemon spawns its own continuation | it verifies with a real model instead of refusing for lack of one | `t94` |
| — | A bridge is asked to listen on port 0 | refused: an ephemeral port cannot be found, stopped or attributed | `t96` |
| — | A foreground `hop` outlives its caller | `ouc stop` reaps it, releases the rotation lock, and says what it stopped | `t97` |
| — | Tunnel mode falls back to Psiphon | WARP's TUN is taken down via `mode proxy` (resolver kept), pin lifted before the probe, opencode handed the bridge, full tunnel guard re-armed on return; a failed fallback leaves WARP up | `t95` |

The live half of this table is run by `.github/workflows/live-verify.yml` on a
disposable root runner (real `iptables`, real `warp-cli`, real
`psiphon-tunnel-core`); the job uploads its evidence JSON as an artifact rather
than asserting success in prose. Row 1–6 live results are whatever that run
measured, not what this table hopes.

## CLI reference

| Command | What it does |
| --- | --- |
| `ouc install` / `uninstall` | Install the supervisor (systemd or portable) / remove everything it created |
| `ouc start` / `stop` | Start or stop the supervisor. `stop` also removes the kill-switch, the bridge and the fallback tunnel |
| `ouc status [--json]` | Supervisor, tunnel, egress, block, backend, guard dialect, pin, blacklist, last error |
| `ouc hop [--backend warp\|psiphon] [--wait N]` | Rotate now, following the ladder (or force one engine) |
| `ouc run -- "<message>"` | Run opencode; on a wall, rotate and continue the same session |
| `ouc resume [-s ses_…]` | Continue the last (or named) session on a working block |
| `ouc agent` | Work through a task list for hours: journal, guardrails, budgets, honest stop |
| `ouc verify [-m model]` | One real request; reports whether the *current block* still answers |
| `ouc repair` | Repair the tunnel and re-arm the firewall |
| `ouc rescue` | Force a working network from any state: guard gone, pin gone, bridge stopped, back on WARP |
| `ouc pin on\|off\|status` | Control the IPv6 `/etc/hosts` pin |
| `ouc killswitch on\|off\|status` | Control `OUC_LEAKGUARD` (`off` also removes the pin) |
| `ouc bridge --listen … [--socks …\|--dynamic]` | Run the HTTP→SOCKS5 bridge |
| `ouc doctor` | Health check, including the fallback backend |
| `ouc config` | Effective configuration as JSON |
| `ouc scan-once` / `lane` / `supervise` / `watchdog` | One detection pass / launch with the pin / run the loop / cron restart guard |

## Configuration

Environment always wins over `config.json`. The ones that matter for the ladder:

| Variable | Default | Meaning |
| --- | --- | --- |
| `OUC_BACKEND_MODE` | `auto` | `auto` follows the ladder · `warp` · `psiphon` pins one engine |
| `OUC_WARP_FALLBACK_THRESHOLD` | `5` | Failed WARP candidates before escalating |
| `OUC_WARP_MODE` | `warp` | `warp` (TUN) or `proxy` (SOCKS5 on `127.0.0.1`) |
| `OUC_PROXY_SOCKS_PORT` | `40000` | WARP's SOCKS5 port |
| `OUC_PROXY_HTTP_PORT` | `8080` | The bridge's listen port (opencode's proxy) |
| `OUC_PSIPHON_BIN` | auto-detected | `psiphon-tunnel-core` path |
| `OUC_PSIPHON_REGIONS` | `US,DE,NL,CA,GB,FR,JP,SG,CH,AT,SE` | Round-robin region list |
| `OUC_PSIPHON_SOCKS_PORT` | `9060` | Psiphon's SOCKS5 port |
| `OUC_PSIPHON_HTTP_PORT` | `9061` | Psiphon's own HTTP port (unused by opencode) |
| `OUC_PSIPHON_START_WAIT` | `45` | Seconds to wait for `Tunnels {"count":1}` |
| `OUC_PSIPHON_CONFIG_SEED` | — | Extra JSON for the generated `psiphon.config` |
| `OUC_AUTO_RESUME` | `0` | `1` lets the supervisor continue the session itself after a wall clears |
| `OUC_AUTO_RESUME_WINDOW` | `900` | Seconds after a wall during which that continuation may still start |
| `OUC_WAIT_FOR_RECOVERY` | `0` | Seconds a managed command waits for a foreign rotation instead of refusing |
| `OUC_ROTATION_TIMEOUT` | `45` | Hard budget for one rotation (never an infinite loop) |
| `OUC_ROTATION_MODE` | `auto` | `keys` (fast) · `register` (different pool) · `auto` (both) |
| `OUC_BLACKLIST_TTL` | `14400` | Seconds a burned block stays burned (4 h) |
| `OUC_MIN_ROTATE_GAP` / `OUC_ROTATE_GAP_JITTER` | `15` / `5` | Deliberate spacing between rotations |
| `OUC_PIN` / `OUC_PIN_AAAA` | on / — | IPv6 pin, and a fixed AAAA list (offline/testing) |
| `OUC_DISABLE_KILL_SWITCH` | `0` | `1` never arms the guard |
| `OUC_TARGET_HOME` / `OUC_TARGET_UID` | — | Which account runs opencode (the proxy guard's scope) |
| `OUC_VERIFY_TIMEOUT` | `120` | Budget for the verifying request |
| `OUC_STATE_DIR` | `/var/lib/opencode-unlimited` | State, locks, pidfiles, logs |
| `OUC_HTTP_PROXY` / `OUC_PROBE_URL` / `OUC_PROBE_CMD` | — | Overrides for the proxy address and the egress probe |

Run `ouc config` for the full list, including the agent budgets
(`OUC_MAX_HOURS`, `OUC_MAX_WALLS`, `OUC_MAX_ROTATIONS`, stall and recycle knobs).

## Docker

There is **no Dockerfile in this repository and no container test in this
matrix** — the badge says "(see note)" for that reason. What a container needs,
if you build one:

* `--cap-add NET_ADMIN` and `--device /dev/net/tun` for **tunnel mode**
  (iptables + TUN). Without them the guard cannot arm and `ouc` will say so
  rather than pretend.
* **Proxy mode** needs neither: `ouc bridge` on `127.0.0.1:8080` and WARP in
  SOCKS5 mode work unprivileged, which is how the Codespaces runs in this
  repository were done.
* `state.json`, locks and pidfiles live in `OUC_STATE_DIR`; mount it if you want
  the blacklist to survive a container restart.

## Honest limitations

1. **The free tier is metered per address, and the allowance is small.** Rotating
   gets a new bucket, not a bigger one. A run that spends several model calls per
   rotation will burn blocks faster than the ladder can replace them. Measured:
   ~4 calls per round exhausted a fresh `/32` mid-round, and at one point all four
   free models were walled from the same WARP address.
2. **Proxy mode's guard scope is the managed account, not the machine.** In
   `proxy` mode the guard blocks *that account's* direct egress; it does not
   sandbox other users or root. Enforcing that would mean a namespace or a
   dedicated uid for the tunnel, which this version does not create.
3. **A wall that arrives while Psiphon is carrying traffic** was measured live,
   but against an *injected* wall event (the exact line opencode writes) plus
   real rotations — not against a genuine multi-hour wall storm.
   That run produced the most useful result in this project's history: the
   supervisor rotated unattended, and then its own auto-resume child was
   refused by the rotation lock it had just taken, so the continuation was
   dropped while the log claimed recovery. `v2.6.1` fixes exactly that (see
   [release notes](#release-notes)); the fix is pinned by four pytest cases but
   its live re-run is still owed.
4. **The 10-hour soak was not run.** Longest continuous live session so far:
   ~4 h wall-clock, 20 rotations, 17 of them on Psiphon.
5. **Stacked transports are acknowledged.** WARP over a host that itself sits
   behind a cloud NAT is what the Codespace tests are; enterprise middleboxes
   that fingerprint the tunnel are not covered.
6. **`ouc hop` must run as root in tunnel mode** (iptables). The unprivileged
   path writes a request file for the supervisor to pick up.
7. **Psiphon's bootstrap values are public constants shipped in every client.**
   They are not secrets and they are not a licence to abuse a provider's free
   tier. You are responsible for the terms you accept.

## Evidence

Every claim above traces to a file produced by a command that ran on a real
machine — never a number typed in by hand. The live harness lives in `tests/lab3/`
(driver, provisioning, and the dead-man's switch that puts the machine back if
a tunnel test severs it); the JSON it writes is uploaded as a workflow
artifact and copied into `tests/lab3/evidence/` for inspection:

```
tests/lab3/evidence/
├── state-pre.json / state-post.json   machine before and after (18-check audit)
├── gap1.json                          wall while Psiphon carries traffic
├── gap2.json                          tunnel-mode ⇄ fallback guard handover
├── chaos1..6.json                     one file per attack
├── audit.txt                          18/18 preservation report
├── supervisor.log                     what the daemon actually said
└── git-verify.json                    tested file == pushed commit
```

A phase that fails writes its failure into the same JSON with the reason, so an
absent file means "not run", not "passed".

### Preservation audit (18 checks, before vs after)

| # | Check | # | Check |
| --- | --- | --- | --- |
| 1 | real internet still works, same IP | 10 | no orphan tunnel process |
| 2 | `/etc/hosts` byte-identical | 11 | fallback SOCKS port closed |
| 3 | `/etc/resolv.conf` byte-identical | 12 | HTTP bridge stopped |
| 4 | IPv4 routes unchanged | 13 | tested controller == pushed commit |
| 5 | IPv6 routes unchanged | 14 | no local-only edits left |
| 6 | IPv4 policy rules unchanged | 15 | escalation on the 5th failed WARP |
| 7 | IPv6 policy rules unchanged | 16 | fallback exit ≠ host's own address |
| 8 | IPv4 firewall unchanged (counters normalised) | 17 | wall rotated with no human involved |
| 9 | IPv6 firewall unchanged | 18 | ≥3 regions carry the same conversation |

## Release notes

| Version | Headline |
| --- | --- |
| **2.6.5** | The tunnel-mode fallback actually moves: WARP's TUN comes down (`mode proxy`, resolver kept) before Psiphon is probed, the pin is lifted before the probe, opencode is handed the bridge while Psiphon carries traffic, and the full tunnel guard is re-armed on return. The exit probe now measures the bridge instead of the host, `OUC_KILLSWITCH=1` re-arms a guard a proxy install disabled, and the rotation budget scales with the region list |
| **2.6.4** | `ouc stop` reaps orphaned tunnels even with a stale pidfile, ownership is the config path not the binary name, and a daemon-spawned continuation no longer refuses for lack of a model |
| **2.6.3** | A cold Psiphon bootstrap is retried once with a longer budget instead of being reported as a provider failure |
| **2.6.2** | A failed escalation hands the machine back honestly: the last walled tunnel is stopped instead of left running, and `active_backend` returns to what was actually carrying traffic |
| **2.6.1** | The daemon can now resume the session it rotated for: a spawned auto-resume waits for the rotation lock instead of refusing it, the resume window covers a full multi-region escalation (`OUC_AUTO_RESUME_WINDOW`, 900 s), and `resume.lock` is released when the child starts |
| **2.6.0** | Tunnel mode ⇄ Psiphon handover fixed (guard dialects, pin handover, fallback respawn); chaos suite `t83`–`t88` |
| **2.5.1** | Five defects that only appear against the real binaries: the `-formatNotices` notice shape, the daemon's write-only stdin (`EBADF`), the tunnel dying with its parent (`EPIPE`), a wall dropped during a repair, a phantom fallback timestamp |
| **2.5.0** | Psiphon as an automatic fallback backend: the escalation ladder, region round-robin, `DisableReplay`, unified `/64`+`/32` blacklist |
| **2.4.x** | Native v2 runner, transport resilience, crash-safe journal, agent budgets |
| **2.3.x** | Non-root ownership, proxy mode, the HTTP↔SOCKS5 bridge |
| **2.2.x** | Kill-switch that can never block its own repair |

Full detail: [CHANGELOG.md](CHANGELOG.md).
Earlier narrative report: [REAL-OPENCODE-TEST.md](docs/reports/REAL-OPENCODE-TEST.md).

---

<div align="center">

**Status: research tool.** It is honest about failing, refuses to claim a
rotation it has not verified, and prefers leaving your internet working to
holding a guard it cannot justify.

</div>
