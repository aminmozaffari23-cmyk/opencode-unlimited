# CHANGELOG — opencode-unlimited

## 2.4.2 — unreleased working tree

The already-unpublished 2.4.2 version/content-signature fix is retained; this
qualification does not introduce another version bump or publish a release.

### Transport resilience follow-up — implementation 89ffea8

- Shared three-additional-attempt transport policy for `agent`, `run`, and
  explicit `resume`; isolated native tool-denied secondary diagnosis, then a
  primary retry or bounded rotation verified against the original model.
- Egress/diagnostic/rotation success does not clear primary task failures;
  transport-only rotation does not mark the old block quota-exhausted.
- Proxy-safe firewall/hosts lifecycle, rotation-spacing lock/deadline cleanup,
  terminal-native-error cleanup, and established-stream timeout correction.
- Final-source 100 focused regressions; 56 safe-default groups pass, 0 fail,
  11 intentionally skipped. 128 local/96 remote simulated subprocess cases,
  4,000 randomized event streams per environment, and a 22-second idle bridge
  test. These are not a real quota exhaustion test or a ten-hour soak.
- Unchanged real MiniMax task: 4/4 in 1,192.031 s. Eight independent browser
  render cases passed; incomplete accessibility checks remain disclosed.
- Original observer failure on 86 ENETUNREACH probes preserved; separate
  read-only adjudication confirmed completion and no successful direct
  connection. No zero-attempt or blanket privacy guarantee is claimed.
- Actual Codespace installation updated from the fetched commit with config,
  native binary, Git dirty tree and registration intact. Final installed task smoke
  15.622 s; ordinary `opencode` entry smoke 11.482 s, without added `--auto`.
  The new Bash function also forwards leading native version/help flags; the
  earlier alias/argparse delivery failure is retained. Controller hash unchanged.
- The final task needed no transport recovery. Full escalation is simulated;
  two intermediate-build real retries are not completed-recovery proof.
  Genuine successful quota recovery/ten-hour live soak remain unqualified.

Current report: [transport-resilience follow-up](docs/reports/transport-resilience-follow-up.md).

### Earlier v2 qualification — historical source 6be5f5d

- Native v2 private-server/JSON launching, scoped proxies, physical cwd/PWD,
  completion/error handling, and exact-session/model continuation.
- Same-model recovery verification; explicit `verify -m`; an unspecified v2
  recovery model stops instead of silently substituting the test model.
- Foreground recovery holds the physical rotation flock, including against an
  older supervisor. Monotonic active timing and owned-process cleanup.
- Historical hop/channel and large-log cursor regressions, protected content
  guards, and the unused doctor-helper logger fix.
- 70 focused simulations pass; externally traced Codespace fixtures made zero
  Internet connects and left shared channels unchanged. Final safe-default
  suite: 55 groups pass, 0 fail, 11 deliberately excluded host-affecting tests.
- A harness fallback-file leak was contained, disclosed, and corrected. Its
  contaminated run is not quota-recovery evidence.
- Actual final-source Beam agent: 4/4 in 337.373 s. MiniMax preflight passed,
  but its full task was observer-cancelled on `provider.transport/ECONNRESET`.
  No successful genuine quota recovery or ten-hour soak is claimed.

Full single-file beginner guide and executed limits: [BEGINNER_GUIDE.md](BEGINNER_GUIDE.md).


## 2.4.1 — 2026-11-12

Two fixes and one feature, all three found by handing a **real website task to
DeepSeek** through `ouc agent` in a Codespace.

  * **A new task no longer inherits the previous journal.** Before this fix,
    starting a second, completely different task in the same account reported
    "3/3 done" in 0.44 s without doing anything, because the journal of an
    earlier run was resumed and all its steps were already marked done. The
    journal is now bound to the task text (sha256) and the work directory; a
    different pair archives the old notebook as `journal-<task_id>.json/.md`
    and starts clean. Journals written before 2.4.1 have no hash, so they are
    kept and stamped instead of being thrown away. Test: `t70`.
  * **`ouc agent -m / --model`** — pick the model for every step
    (`OUC_AGENT_MODEL` also works), instead of being stuck with the default.
  * Suite is now **58 offline tests, 0 failures**.

Measured in this release (see `TEST_RESULTS.md`): a 3-page Tailwind site built by
`opencode/deepseek-v4.1-flash-free` through `ouc agent` — **4/4 steps, 11 m 29 s,
0 quota walls**, three served files (HTTP 200), one git checkpoint.

## 2.4.0 — 2026-11-12

Built for one purpose: let an agent work for **10 hours without a human**, and
be honest about what happened afterwards.

**Resilience**
  * `ouc agent --task FILE [--max-hours 10]`: a durable journal
    (`journal.json` + readable `journal.md`), a step that was in flight when the
    process died is retried instead of lost, git checkpoint after every step.
  * `ouc watchdog`: restarts the supervisor if it died or stopped breathing
    (heartbeat older than 90 s). Installed as a 2-minute cron entry in portable
    mode; `systemd` mode gets `Restart=always`, `RestartSec=5`,
    `StartLimitIntervalSec=0`.
  * Failure taxonomy — four kinds of "no", four behaviours: quota wall (rotate +
    resume), rate limit (wait 60→120→240→480→900 s, never rotate), network
    (retry 30 s up to 10 min, never rotate), auth/model error (stop immediately).
  * Stall detection: 600 s without a log byte or CPU time → kill, retry once,
    then mark the step failed and move on.
  * Conversation recycling every 3 h / 2 MB / 40 turns (max 8), so a long run
    does not drown in its own context.
  * Guardrails: force-push, git history rewrite, `/etc/hosts`, firewall edits,
    SSH keys, package removal and `curl | sh` are refused before the run starts,
    and protected paths are audited after every step.

**Budgets, as configured for this release**
  * rotation spacing **15–20 s** (15 s + 0–5 s jitter);
  * **no limit** on walls, rotations per hour, rotations per run or resumes
    (`OUC_MAX_WALLS=0`, `OUC_ROTATE_PER_HOUR=0`, `OUC_MAX_ROTATIONS=0`,
    `OUC_RESUME_MAX=0`);
  * **60** consecutive failures before it stops (`OUC_MAX_CONSEC_FAIL=60`);
  * run length **10 h** (`OUC_MAX_HOURS=10`);
  * the "sleep until the quota window reopens" option is **removed**
    (`OUC_RESUME_WAIT_RESET` no longer exists).

**Verification** — executed, not reviewed: 57 offline tests, 0 failures; a chaos
run with 8 injected faults (all recovered, worst case 122 s) plus a 20-wall soak
with production spacing (333 s); details in `TEST_RESULTS.md`.

## 2.3.1 — 2026-11-12

Fix found by a real install in a fresh Codespace: `ouc run` / `ouc resume` hit
`PermissionError: /var/lib/opencode-unlimited/state.lock` whenever the install
was done with `sudo` (as the docs say) and the command was then run as the
normal user.

  * `install.sh` now hands the state directory to the user who invoked `sudo`
    (`SUDO_UID`/`SUDO_GID`), so the daily commands work without `sudo` while the
    root supervisor keeps full access. Re-running the installer repairs an
    already-broken ownership.
  * `StateLock` creates the lock file mode 0666, so it does not matter whether
    root or the user created it first.
  * A non-writable state directory now produces one readable line with the fix
    instead of a Python traceback (`ouc run` / `ouc resume` guard).
  * New test `t51` covers exactly this: lock mode under `umask 077`, an
    unprivileged user locking a root-owned lock file, and the honest error path.
  * Suite: 39 offline tests, 0 failures.

## 2.3.0 — 2026-11-12

### Auto-resume: hit the free limit, wait for a working block, continue the work

The feature, in the words it was asked for: *when the free limit is reached,
wait, check that the next WARP block actually works, then tell the model to
continue where it left off.*

New commands:

| command | what it does |
|---|---|
| `ouc run [-- opencode args…]` | runs `opencode run`, watches the log for the quota wall, then rotates until a block **verifies with a real request**, and relaunches the *same* session instead of a new one. Repeats up to `OUC_RESUME_MAX` times. |
| `ouc resume [-s ID] [-m MODEL] [-p PROMPT]` | the same continuation, without launching anything first: verify the current block, find another if needed, then continue. |

Design rules that are enforced, not aspirational:

1. **Never resume on an unverified block.** `rotate()` already proves every
   candidate with a real request; `rotate_until_healthy()` accepts only that.
2. **Never resume twice for one wall.** Wall counters live in `state.json`,
   and the supervisor path takes a `resume.lock` flock.
3. **Never loop forever.** `OUC_RESUME_MAX` attempts (default 3) and
   `OUC_RESUME_BUDGET` seconds (default 900) bound the whole thing, and the
   failure message says what to do next.
4. **Never invent a session.** The default continuation is `-c` (continue the
   last session in this directory) because a scraped `ses_…` id can belong to
   another directory or to opencode's title agent — which fails with
   *Session not found*. `-s <id>` is opt-in (`OUC_RESUME_USE_SESSION_ID=1`) and
   automatically falls back to `-c` if opencode rejects the id.
5. **The supervisor stays off this path by default.** `OUC_AUTO_RESUME=1`
   enables it for the background supervisor; the default off is deliberate,
   because a user sitting in front of their own opencode session would get a
   duplicate conversation.

Configuration (env or `config.json`):

| variable | default | meaning |
|---|---|---|
| `OUC_RESUME_MAX` | 3 | maximum continues per `ouc run` |
| `OUC_RESUME_BACKOFF` | 5 | seconds between failed attempts |
| `OUC_RESUME_BUDGET` | 900 | total seconds allowed for recovery |
| `OUC_RESUME_PROMPT` | built-in text | what to send; it asks for continuation, not a restart |
| `OUC_RESUME_MODEL` | from the wall line | model to continue with |
| `OUC_RESUME_USE_SESSION_ID` | 0 | continue by `-s <id>` instead of `-c` |
| `OUC_RESUME_WAIT_RESET` | 0 | if no block works, wait for the window (uses `Retry-After`) |
| `OUC_AUTO_RESUME` | 0 | let the *supervisor* resume after a wall |

### Verification

* offline suite: **38 passed, 0 failed, 0 skipped** (four new tests: resume
  after a wall, no resume without a wall, honest give-up after `OUC_RESUME_MAX`,
  and `ouc resume` verifying before continuing; plus the `-s` opt-in path).
* real end-to-end, 2026-11-12, WARP proxy mode + opencode 1.19.5: a wall was
  detected, the tool rotated to a **verified** block
  (`2a09:bac5:b042:1caa`), relaunched the session with `-c`, and the real model
  answered — `resumes=1, walls_seen=1, hops=1`, exit 0.

### Fixed

* `ouc run` no longer mislabels a failed `-s` resume as "no quota wall": the
  fallback to `-c` is explicit in the log and on stderr.
* Test harness portability: the `sys.path` entry and the default log path no
  longer hardcode `/home/user/...`.

## 2.2.0 — 2026-11-08

### Packaging (2026-11-10)

* `INSTALL.md` rewritten for 2.2.0 as one all-platform guide: Linux (systemd and
  portable), Codespaces/Docker/CI proxy mode, Windows via WSL2, macOS via
  Docker — with a glossary, a platform decision table, troubleshooting and a
  "hand this to someone else" section.
* Portability fixes in the test harness: removed a hardcoded
  `/home/user/build/...` entry from `tests/run_tests.sh`'s `sys.path`, and
  `tests/real-limit-test.sh` now writes its log to `${TMPDIR:-/tmp}` instead of
  a fixed home directory.
* Zip rebuilt; offline suite re-run after the edits: **33 passed, 0 failed,
  0 skipped**.

The same tool, on machines where you **cannot** create a TUN device and cannot
touch the firewall: GitHub Codespaces, devcontainers, unprivileged containers,
CI runners. Nothing was cut — rotation, wall detection, verification, `hop`,
`verify`, `status`, `doctor`, the blacklist and the supervisor all still run.
Only the *transport* changes: instead of capturing packets, the tunnel exposes a
SOCKS5 proxy and the tool bridges it to HTTP for opencode.

### Added

- **Proxy mode (no TUN, no firewall).** `OUC_WARP_MODE=proxy` — chosen
  automatically on `CODESPACES=true`, as non-root, when `/dev/net/tun` is
  missing, or when `iptables` is unusable ("auto"). It runs
  `warp-cli mode proxy` + `warp-cli proxy port 40000`, so WARP listens on
  `127.0.0.1:40000` and no route, TUN device or nftables/iptables rule is
  touched. `mode` and the port are re-asserted at supervisor start-up and
  before every rotation, because a fresh container comes back in the default
  (TUN) mode and mode drift is silent.
- **Embedded HTTP→SOCKS5 bridge** (`ouc bridge`, class `Bridge`): a tiny
  `HTTPS CONNECT` relay on `127.0.0.1:8080` in front of the WARP SOCKS5 port.
  Needed because opencode's HTTP client rejects `socks5://` outright
  (`UnsupportedProxyProtocol`); it accepts `http://` fine. Started by the
  supervisor, restarted on demand by `rotate()`, verified by the test suite
  with a real CONNECT request end to end.
- **Proxy-aware egress probes.** Every probe (`probe_egress`, the stdlib
  fallback, verification, `doctor`) now goes *through* the bridge in proxy
  mode. Without this a "rotation" compared the container's own IPv4 address
  with itself and reported success while nothing had changed.
- **`ouc lane` in proxy mode**: execs opencode with `HTTPS_PROXY` /
  `HTTP_PROXY` pointing at the bridge, still never touching its output.
- **Kill-switch and `/etc/hosts` pin auto-disable** in proxy mode, with the
  reason recorded in state (`no TUN, so a kill-switch would only break the
  container's own networking`). `ouc doctor` prints the same reason, so "off"
  never looks like a failure.
- **`install.sh --proxy`**, and `--portable` (no systemd unit) for containers
  where systemd is not PID 1.
- **Five new offline tests (t40–t44)** and one new live test (t45):
  proxy-mode config, rotation + verification through the HTTP proxy, a real
  HTTP→SOCKS5 CONNECT exchange, `status`/`doctor` in proxy mode, and
  "the installer never arms the firewall in proxy mode" (asserted by counting
  calls to a fake `iptables`).

### Fixed

- **A second supervisor could start after its state directory was wiped.**
  The instance lock moved from the state dir to a fixed path
  (`/run/opencode-unlimited.lock`, override `OUC_INSTANCE_LOCK`), so
  `rm -rf /var/lib/opencode-unlimited` no longer allows a duplicate daemon
  fighting over the proxy port.
- **`install.sh` no longer deletes a live WARP registration.** It checks
  `warp-cli registration show` first; the old code re-registered on every run
  and made a merely-slow reconnect look like a broken account.
- **`install.sh` stops the previous supervisor before installing.** The old
  pattern matched `ouc.py supervise`, which the portable daemon never runs as
  (it is `ouc.py start --portable`), so re-installs left two supervisors and
  two bridges behind.
- **`Firewall.installed()` returns immediately in proxy mode** — it no longer
  shells out to `iptables -C` on a machine where no guard can exist.
- **Rotation races**: `ouc hop` while the supervisor is already rotating now
  says `rotation already in progress` instead of half-running a second
  rotation.

## 2.1.0 — 2026-11-08

Everything here was found by measuring a live machine — burning the real free
tier, testing each rotation mechanism and timing it. The research write-up is
`opencode-quota-enforcement-research.md` in the parent directory.

### Added

- **IPv6 pinning** (`Pinner`, `ouc pin status|on|off`). opencode prefers
  `104.20.32.17:443` over IPv4 whenever it can, and WARP's IPv4 egress is a
  **5-address shared NAT pool** — so rotating `/64`s changed nothing for most
  users. The supervisor now keeps a managed block in `/etc/hosts` mapping
  `opencode.ai` and `api.opencode.ai` to the tunnel's Cloudflare IPv6 edge
  addresses, resolved over DNS-over-HTTPS (so the pin cannot feed back on
  itself). Verified with `ss -tnp`: with the pin, the only `:443` socket open
  during a request is `[2606:4700:10::ac42:ad95]:443`.
- **`warp-cli tunnel rotate-keys`** as the rotation path: new key pair, same
  registration, **2–4 s** end to end versus 17–25 s for
  `registration delete` + `new`. The old path is kept as an automatic fallback
  (`OUC_ROTATION_MODE=auto|keys|register`).
- **Request-verified rotation.** `verify_request()` makes one real
  `opencode run` and only reports success when opencode answers. A rate-limited
  block usually does not error — it **hangs** — so verification watches the
  opencode log while the request runs and aborts the instant a quota line
  appears instead of waiting for a timeout. When running as root the request is
  demoted to the target user first (root's `$HOME` makes `opencode run` hang).
- **`ouc verify`**: one request, honest answer, exit 0/1/2 (healthy / walled /
  could not test).
- **Spent-block blacklist with a 4 h TTL** (`OUC_BLACKLIST_TTL`, timestamps in
  `state.json`), and a fallback: if no candidate can be confirmed the last one
  is kept and `ouc hop` is suggested, rather than leaving the user on the block
  that is definitely walled.
- **Escalation ladder**: rotate-keys (up to `OUC_KEYS_ATTEMPTS`, default 4) →
  re-registration → give up online with a real error message.

### Changed

- Blacklist TTL default 24 h → **4 h**, matching the measured window.
- `ouc status` shows the pin (installed? which addresses?) and the last
  rotation (how, how long, verified?).
- `ouc stop` / `ouc uninstall` remove the pin, so `/etc/hosts` is left byte-for-
  byte as it was found.

### Fixed

- `rotate()` could return `None`, which crashed `ouc hop` with an
  `AttributeError`. It now always returns a result, and an unexpected exception
  is logged with a traceback instead of vanishing.
- A pin without a working IPv6 path would have broken `opencode.ai` entirely
  (`curl: (7)`). Installation is now conditional on a resolved IPv6 address and
  the supervisor removes the pin the moment the tunnel drops.

## 2.0.1 — 2026-11-08

Found by running the tool against **real opencode 1.19.5 traffic** until the
free-tier limit was genuinely exhausted, not by reading the code. Full
timeline in `TEST_RESULTS.md`.

### Fixed

- **`ouc status` reported a healthy kill-switch as missing** when run as a
  normal user: `iptables -C` needs root, the query failed, and the failure was
  reported as "NOT installed". The supervisor now records `guard_installed` in
  `state.json`; an unprivileged `status` shows that record and labels it,
  `sudo ouc status` still asks the kernel directly.
- **The `/64` blacklist lost its own entries.** Blocks are stored as bare
  strings (`2a09:bac5:b045:1caa`) but were re-validated with `norm64()`, which
  expected a full address, so all but the newest entry were dropped on every
  rotation. `norm64()` now accepts both forms. Symptom before the fix:
  `rotations: 10, blacklisted blocks: 0`.
- **Rotations could land you on a block that was already exhausted.** The tool
  accepted any `/64` different from the current one. Cloudflare recycles
  blocks, so late in a busy day every candidate had already been burned and a
  "successful" rotation left you limited on the very next request. Rotation now
  consults the blacklist (24 h TTL, `OUC_BLACKLIST_TTL`), rejects spent blocks
  and asks for another one; if it cannot find a fresh block it keeps the tunnel
  up and says so instead of pretending to have fixed it.

### Added

- `t23_blacklist_skips_spent_block` — deterministic offline test for the new
  behaviour (seeds a spent block, asserts the rotation skips it).
- `REAL-OPENCODE-TEST.md` — the real opencode 1.19.5 run, with verdict and limits.
- `tests/real-limit-test.sh` — drives real opencode traffic to the real limit
  and reports detection latency, rotation time and the resulting `/64`, all
  taken from the two logs.
- `state.json` now stores `blacklist_ts` (when each block was burned) and
  `guard_installed`.

### Real-world numbers from this session

| Measurement | Value |
|---|---|
| Wall logged by opencode → detected by the supervisor | **2 s** |
| Wall logged → fresh working `/64` | **19 s** |
| Rotation that had to reject an already-spent `/64` | 33.6 s (2 cycles) |
| Rotations during one heavy day | 20 |
| Guard lifted during repair, restored after | yes, +2 s |

### Documented limitation (not fixable here)

After ~20 rotations in one day Cloudflare had no unspent `/64` left for this
device: every candidate it returned was already burned. The tool now detects
that and reports it. Also, opencode itself retries for 88–300 s before it
prints the limit error, so the failing request stays on screen long after the
rotation has already succeeded — resending is what makes it work.

## 2.0.0 — 2026-11-08

A rewrite, not a patch. Everything below was found by running the tests in
`TEST_RESULTS.md`; the raw logs from the release run are in `tests/logs/`
(**27 passed, 0 failed, 0 skipped**).

### Removed (the things that made the original unusable)

- **All advertising.** No `~/.opencode/AGENTS.md` is created, no text or
  signature is appended to model output, and the wrapper never filters,
  pipes or rewrites a single byte. The old file is recognised by a
  base64-encoded marker (so the repository itself contains no ad text) and
  deleted on install and uninstall. There is no watchdog that puts it back.
- **The sudoers drop-in.** `/etc/sudoers.d/opencode-unlimited` granted
  passwordless root for a user-writable script. v2 installs no sudoers file
  at all and deletes the old one.
- **The systemd *user* session requirement.** v2 runs either as a root
  systemd service (no user session needed) or as a portable supervisor with
  a PID file, lockfile and rotating logs.
- **The self-restoring keeper service** and the hijacked
  `~/.local/bin/opencode` entry point.

### Fixed

- **Quota detection actually fires.** opencode 1.19 writes
  `AI_APICallError: Free usage limit reached…`; the old code only matched
  `QuotaExceeded`, so it never triggered. Both are matched now.
- **Detection is idempotent.** State keeps a byte offset plus the SHA-256 of
  the 64 bytes before it, so the same wall cannot fire twice, and a log that
  is truncated *to the same length* is still detected as rewritten. A new or
  shrunk log re-reads from zero. The first pass after a restart baselines at
  the end of the log, so yesterday's quota message no longer triggers a
  rotation on boot.
- **Rotation respects Cloudflare's timing.** `registration delete` is always
  followed by a settle wait (`OUC_SETTLE_SECONDS`, default 9, never below 8)
  before `registration new`, with bounded retries and doubling backoff
  (`OUC_REG_RETRIES`, `OUC_REG_BACKOFF`). Measured rotation: 17–20 s.
- **Cloudflare handing back the same /64** is retried as a whole cycle
  instead of failing the rotation (`OUC_SAME_BLOCK_RETRIES`).
- **The kill-switch can no longer cut you off.** The old flow blocked
  traffic with iptables and then needed the same network to repair the
  tunnel — a hard deadlock. v2 lifts `OUC_LEAKGUARD` *before* any teardown,
  reconnects, and only re-arms once a real IPv6 egress is verified. If the
  repair fails, the rules stay off, `degraded=true` is recorded and a
  `SECURITY` line is logged — your internet is never sacrificed.
- **Recovery paths that always work:** `ouc repair`, `ouc rescue` (works
  with a corrupt state file), `ExecStopPost` for crashes, `SIGTERM` handler,
  and the six commands documented in `UNINSTALL.md`.
- **`ouc hop` as a normal user** now reaches the root supervisor through a
  request/result file, with a writable fallback channel when `~/.local` is
  root-owned.
- **`mode` is recorded at install time** and auto-detected from the installed
  unit otherwise; `status`, `hop` and `doctor` share one liveness check, so a
  healthy systemd install is never reported as "portable, not running".
- **Empty /64s are never blacklisted**: an unprobeable or unknown egress is
  simply not recorded.
- **Cooldown and pending state**: a wall detected during the cooldown is
  served when the cooldown expires instead of being forgotten.
- **Corrupt `state.json`** is recovered rather than fatal.
- **Log files rotate** instead of growing without bound.
- **Installer is idempotent** — a second install keeps the same supervisor
  PID and cleans up v1 leftovers first.
- **Uninstall is complete** — unit, program, state, firewall, hop channels,
  hop fallback dir, v1 unit, v1 keeper, v1 sudoers, v1 launcher, ad file.

### Added

- `ouc status` with a `--json` mode, `ouc doctor`, `ouc config`,
  `ouc killswitch on|off|status`, `ouc repair`, `ouc rescue`, `ouc scan-once`.
- Two install modes with explicit switching: `--portable`,
  `OUC_FORCE_MODE=systemd|portable`.
- `install.sh --with-warp`, `--with-opencode`, `--wrap`, `--yes`.
- A test suite: 22 offline tests driven by deterministic fakes (no network,
  no root changes) and 5 tests against a live Cloudflare WARP tunnel.
- Documentation set: `README.md`, `INSTALL.md`, `UNINSTALL.md`,
  `SECURITY.md`, `TEST_RESULTS.md`, this file.

### Verified on the release machine

| Metric | Result |
|---|---|
| Rotation (real, automatic) | 20 s (`2a09:bac5:b044:3705` → `2a09:bac5:b044:119`) |
| Rotation (real, manual `ouc hop`, unprivileged user) | 16.9 s (`2a09:bac5:b042:154b` → `2a09:bac5:b045:3705`) |
| Recovery from a destroyed tunnel under an armed guard | 38 s, guard re-armed automatically |
| Settle wait enforced | 9 s, zero rejected deletions |
| Wrapper byte-transparency | identical SHA-256, including a missing trailing newline |
| Test suite | 27 passed, 0 failed, 0 skipped |
