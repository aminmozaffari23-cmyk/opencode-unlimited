# opencode-unlimited 2.3.1

Keeps your Cloudflare WARP tunnel up, and when opencode's free tier says
**“Free usage limit reached”**, quietly moves the machine to a fresh IPv6
block so you get a fresh quota — without ever cutting your internet and
without touching a single byte of opencode's output.

This is a corrected rewrite of the original `opencode-unlimited-usage`
script. Every claim in this README was verified by the tests in
[`TEST_RESULTS.md`](TEST_RESULTS.md); the raw logs are in `tests/logs/`.

```
33 passed, 0 failed, 0 skipped     (offline suite, incl. 5 new proxy tests)
6 passed                           (live: 5 tunnel-mode + 1 proxy-mode)
```

Plus real end-to-end runs against **opencode 1.19.5 itself**. A genuine wall —
the free tier exhausted by 47 real requests — was detected and recovered from in
**7 s** (rotation + a real request proving the new block answers). Manually:
`ouc hop` → **7.06 s**, exit 0, first attempt. Timeline and logs:
`TEST_RESULTS.md`.

## Two ways to run it

| | **tunnel mode** (default) | **proxy mode** (`--proxy`) |
|---|---|---|
| Needs root | yes | no (but WARP's own daemon may) |
| Needs `/dev/net/tun` | yes | **no** |
| Changes routes / iptables / nftables | yes (kill-switch) | **never** |
| How opencode reaches the internet | transparently, via WARP | `HTTPS_PROXY=http://127.0.0.1:8080` (or `ouc lane`) |
| Rotation, wall detection, verify, hop, status, doctor | ✓ | ✓ (identical) |
| Systemd | optional (`--portable` to skip) | usually `--portable` |
| Target | normal Linux box / VM | **GitHub Codespaces, devcontainers, CI** |

Proxy mode is picked automatically when `CODESPACES=true`, when you are not
root, when `/dev/net/tun` is missing, or when iptables cannot be used. Nothing
is removed: it is the same supervisor, the same rotation paths, the same
verification — only the transport differs. Because opencode's client refuses
`socks5://` (`UnsupportedProxyProtocol`), the tool bridges WARP's SOCKS5 port
to a local HTTP proxy itself; that bridge is what `HTTPS_PROXY` points at.

```bash
# Codespaces / container / no TUN:
sudo bash install.sh --proxy --portable
ouc status                 # shows: warp mode: proxy (no TUN), bridge, egress family
ouc lane run "hello"       # opencode, routed through WARP
```

See [`INSTALL.md`](INSTALL.md#proxy-mode-codespaces-devcontainers-ci) for the
Codespaces walk-through.

## What changed in 2.3.0

`ouc run` — one command that keeps your work going:

```
ouc run "refactor the parser and run the tests"
```

If opencode's free limit appears mid-task, the tool waits for a WARP block that
**answers a real request**, then continues *the same session* (`-c`, or `-s` if
you opt in) with a prompt that says "continue where you stopped" rather than
"start over". Bounded by `OUC_RESUME_MAX` (3) and `OUC_RESUME_BUDGET` (900 s);
`ouc resume` does the same thing on demand. Full detail: `CHANGELOG.md`.

## What changed in 2.2.0

- **Proxy mode**: `warp-cli mode proxy` + an embedded HTTP→SOCKS5 bridge, so
  the tool runs where TUN and firewall changes are impossible. Kill-switch and
  the IPv6 pin disable themselves (and say why).
- **Proxy-aware probes**: egress, verification and `doctor` all measure through
  the bridge — the old code measured the container's own address and reported
  a rotation that never happened.
- **Robustness found by testing**: one supervisor per machine even after its
  state dir is wiped (lock in `/run`), the installer no longer deletes a live
  WARP registration or leaves a second daemon behind, and `hop` while the
  supervisor is busy reports it instead of racing it.

## What changed in 2.1.0

Everything below came from measuring the real thing, not from reading code.

| v2.0.1 | v2.1.0 | Why |
|---|---|---|
| Rotated the tunnel and called it a day | **Forces opencode onto IPv6** (`/etc/hosts` pin) | opencode dials `104.20.32.17` over IPv4 when it can. WARP's IPv4 egress is a 5-address shared pool, so rotating `/64`s did nothing until IPv6 was forced |
| `registration delete` + `new` (17–25 s) | **`warp-cli tunnel rotate-keys`** (2–4 s), re-registration as fallback | Same effect, one API call, no settle wait |
| "Success" = the egress address changed | **Success = a real request answered** (`ouc verify`) | Cloudflare recycles blocks. `76c0:dba8` came back already burned minutes after we left it |
| Blacklist with a 24 h TTL | **4 h TTL** | Measured: a burned block is usable again after ~4 h |
| — | **`ouc pin status\|on\|off`**, **`ouc verify`** | See the state, prove the quota, undo in one command |

The pin is removed automatically whenever the tunnel is down, so a failed
rotation can never leave you with a broken `opencode.ai` resolution.

---

## What was wrong with the original

| Original behaviour | Why it hurt | v2 |
|---|---|---|
| Wrote `AGENTS.md` and appended a Telegram ad to model output | Injected advertising into your code and prompts | Nothing is written, nothing is appended, wrappers are byte-transparent (t01, t02) |
| Recognised only `QuotaExceeded` | opencode 1.19 writes `Free usage limit reached` — so it never fired | Both wordings detected (t03, t05) |
| `registration delete` immediately followed by `new` | Cloudflare rejects it; rotations failed | Waits 9 s (configurable, ≥ 8 enforced), bounded retries with backoff (t08, t09) |
| Blocked traffic with iptables, then needed the same network to repair the tunnel | **Deadlock: no internet, no recovery** | Rules are lifted before any teardown; if the tunnel does not come back the box stays online and says so (t10, t32) |
| Needed `systemd --user` | Fails on servers, Docker, CI, SSH-only boxes | Root systemd unit **or** portable supervisor with PID file + lockfile (t13, t18) |
| Installed `/etc/sudoers.d/opencode-unlimited` with blanket `NOPASSWD` | A user-writable script running as root | No sudoers file at all; privileged work is a root service (t21) |
| Wrapper mangled/piped output | Broke tools, diffs and copy-paste | `exec` only; byte equality asserted (t02) |

Details and the full list of bugs the test suite caught: `TEST_RESULTS.md` §3.

---

## Requirements

| Needed | Why |
|---|---|
| Linux with **IPv6** | WARP hands out an IPv6 `/64`; that block is the quota bucket |
| `cloudflare-warp` (`warp-cli`) | The tunnel that gets rotated |
| `iptables` + `ip6tables` + `/dev/net/tun` | Only for the optional kill-switch; install still works without them |
| Python 3.8+ | Everything is standard library, single file |
| opencode CLI | Optional — the tool only reads its log |

Root is required to install (a system service is created). The day-to-day
`ouc hop` works as a normal user.

---

## Install

```bash
sudo bash install.sh              # systemd if available, portable otherwise
sudo bash install.sh --portable   # force the no-systemd supervisor
sudo bash install.sh --with-warp  # also install cloudflare-warp if missing
```

Full options, modes and troubleshooting: [`INSTALL.md`](INSTALL.md).
Removal: [`UNINSTALL.md`](UNINSTALL.md).

## Daily use

```bash
ouc status      # health: supervisor, tunnel, egress /64, pin, guard, rotations
ouc hop         # ask the supervisor for a fresh /64 right now (no root needed)
ouc verify      # make ONE real request: is the quota actually working here?
ouc pin status  # is opencode being forced onto IPv6? (sudo ouc pin on|off)
sudo ouc repair # force a tunnel repair, then re-arm the kill-switch
sudo ouc rescue # emergency: stop everything, make sure the internet works
sudo ouc doctor # full diagnostic report
sudo ouc config # effective configuration and where each value comes from
```

`ouc status` at a glance:

```
opencode-unlimited 2.2.0  (mode: systemd)

✓ supervisor: running (pid 18702)
✓ warp tunnel: Status update: Connected
✓ egress: 2a09:bac1:7680:dba8::57b:2d   block: 2a09:bac1:7680:dba8
✓ kill-switch: installed
✓ rotations: 3   blacklisted blocks: 3
```

In proxy mode the same command adds, and swaps, exactly two lines:

```
✓ warp mode: proxy (no TUN) — SOCKS5 127.0.0.1:40000
✓ http bridge: http://127.0.0.1:8080 (opencode uses it)
```


## How it works

```
opencode writes ~/.local/share/opencode/log/opencode.log
        │
        ▼
supervisor (root, systemd unit or portable daemon, ticks every 5 s)
        │  byte-offset cursor + tail hash  → idempotent detection
        ▼
"Free usage limit reached" seen  ──►  cooldown  ──►  rotation
                                                       │
   1. lift the kill-switch (so DNS and the WARP API stay reachable)
   2. warp-cli disconnect
   3. warp-cli registration delete        ← the old /64 is now dead
   4. wait 9 s                            ← Cloudflare needs 8–10 s
   5. warp-cli registration new           ← fresh device = fresh /64
   6. warp-cli mode warp && connect
   7. verify a real IPv6 egress; retry the whole cycle if Cloudflare
      handed the same /64 back (bounded)
   8. re-install the kill-switch, record the old /64 in the blacklist
```

If step 7 cannot be satisfied: the rules stay lifted, `degraded=true` is
written to `state.json`, and a `SECURITY` line is logged. **Your internet
wins over your privacy, always.** See [`SECURITY.md`](SECURITY.md).

Measured on the test machine: rotation 18–20 s, recovery from a killed
tunnel 38 s, manual `ouc hop` 16.9 s, and with real opencode traffic
**2 s to detect + 7 s to rotate and verify**. Measured repeatedly; see
`TEST_RESULTS.md` for the raw timeline.

## The kill-switch (optional)

While the tunnel is up, `OUC_LEAKGUARD` rules in `iptables`/`ip6tables`
block traffic that would leave through the real interface, so a WARP drop
does not silently leak your real IP. During a repair those rules are removed
and your real IP is visible for a few seconds — unavoidable, logged, and
documented. Disable the whole thing with:

```bash
sudo ouc killswitch off          # immediately
OUC_DISABLE_KILL_SWITCH=1        # permanently, in the unit
```

## Files it creates

| Path | What |
|---|---|
| `/usr/local/bin/ouc` | tiny shell entry point that `exec`s the program below |
| `/usr/local/lib/opencode-unlimited/ouc.py` | the single-file program |
| `/etc/systemd/system/opencode-unlimited.service` | root unit (systemd mode) |
| `/var/lib/opencode-unlimited/` | `state.json`, `heartbeat`, `supervisor.log`, `ouc.log`, `supervisor.pid`, `rotation.lock` |
| `~/.local/state/opencode-unlimited/hop.{request,result}` | unprivileged → root hop queue |

Nothing else. No `AGENTS.md`, no dotfile injection, no shell profile edits,
no sudoers file, no `opencode` replacement unless you pass `--wrap`.

## Configuration

Everything is environment-driven and can be set in the unit
(`systemctl edit opencode-unlimited.service`):

| Variable | Default | Meaning |
|---|---|---|
| `OUC_STATE_DIR` | `/var/lib/opencode-unlimited` | state, logs, pidfile |
| `OUC_TARGET_HOME` | the installing user's home | where opencode's log lives |
| `OUC_SETTLE_SECONDS` | `9` | wait between delete and new (never below 8) |
| `OUC_COOLDOWN_SECONDS` | `60` | minimum gap between rotations |
| `OUC_REG_RETRIES` / `OUC_REG_BACKOFF` | `3` / `3` | registration retries and doubling backoff |
| `OUC_SAME_BLOCK_RETRIES` | `2` | cycles when Cloudflare returns the same /64 |
| `OUC_ROTATION_TIMEOUT` | `45` | hard budget for one rotation |
| `OUC_TICK_SECONDS` | `5` | supervisor loop period |
| `OUC_DISABLE_KILL_SWITCH` | unset | `1` = never touch the firewall |
| `OUC_PROBE_URL` / `OUC_PROBE_CMD` | `https://api64.ipify.org` | how the egress address is measured |
| `OUC_FORCE_MODE` | unset | `systemd` or `portable` |

`sudo ouc config` prints the effective values and their origin.

## Tests

```bash
bash tests/run_tests.sh --offline     # 22 tests, no WARP, no network changes
sudo bash tests/run_tests.sh          # offline + 5 real tests on a live tunnel
bash tests/run_tests.sh --select=t31  # a single test
```

Real tests `SKIP` (never fake a pass) on machines where WARP cannot connect.
Results of the shipped run: [`TEST_RESULTS.md`](TEST_RESULTS.md).

## Honest limitations

- The quota bucket is Cloudflare's per-`/64` request allowance (~48–80
  requests per window, measured). It is not a documented contract and
  Cloudflare can change it; detection only reacts to opencode's own message.
- Rotation needs working IPv6. Without it `ouc hop` reports the failure and
  leaves your internet alone.
- **Cloudflare recycles `/64`s.** Quota is per block per window, so late in a
  heavy day it starts handing back blocks whose quota is already spent. The
  tool skips the ones it remembers and tells you when it runs out of better
  options — but it cannot create quota Cloudflare is not giving out.
- **opencode retries before giving up.** After the limit is hit, the request in
  flight can sit there for a minute or more before opencode prints the error.
  That request still fails; resend it and it goes out on the new block.
- The supervisor rotates at most once per `OUC_COOLDOWN_SECONDS` (90 s in the
  default install), so a wall hit seconds after a rotation waits for the next
  one.
- The kill-switch cannot hide your real IP while the tunnel is down. That
  trade-off is deliberate and visible: see `SECURITY.md`.
- Only opencode's own log is watched. Other clients hitting the same account
  are not.

## Licence and provenance

Rewritten from scratch after auditing the original repository. No original
code, advertising text, or agent-instruction file is carried over; the only
reference to the old version is a deletion marker so uninstall can clean it
up.
