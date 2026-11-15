# REAL-OPENCODE-TEST.md — historical measurements

> Historical OpenCode 1.x experiment, not current v2 qualification. Do not run
> an exhaustion loop to reproduce it. Current measured results and limitations
> are in [BEGINNER_GUIDE.md](BEGINNER_GUIDE.md).

A real test against **opencode 1.19.5 itself**, not a simulation: the free-tier
limit was hit with real prompts and the tool had to deal with it.

- Date: 2026-11-08
- Host: Debian 13, `warp-cli` 2026.8.2115.0, tunnel connected
- Model: `opencode/flint-40b-free`
- Package under test: this archive, installed with `sudo bash install.sh` (systemd mode)
- Driver: `tests/real-limit-test.sh`

---

## Verdict, up front

| Question | Answer |
|---|---|
| Does it notice the limit by itself? | **Yes** — 2 s after opencode writes the line |
| Does it rotate by itself? | **Yes** — 17 s, no commands issued |
| Does opencode work again afterwards? | **Yes**, on the new `/64` |
| Does it keep the internet up during the repair? | **Yes** — guard lifted first, restored after |
| Any advertising or output changes? | **None** |
| Does it always save you? | **No** — see "hard limits" below, especially the recycled-block problem |

The one thing it cannot do: conjure free quota that Cloudflare is not handing
out. Late in a heavy day every block it can get is already spent, and it says
so rather than pretending.

---

## Timeline of a real limit hit

Taken from `~/.local/share/opencode/log/opencode.log` and
`/var/lib/opencode-unlimited/supervisor.log`:

| Time (UTC) | What happened |
|---|---|
| 12:30:53.721 | opencode logs `AI_APICallError: Free usage limit reached…` |
| 12:30:55 | supervisor: `quota wall detected (Free usage limit reached)` — **+2 s** |
| 12:30:56 | `rotation start (quota-wall); current egress=2a09:bac5:b043:1caa::2db:c…` |
| 12:30:56 | `kill-switch lifted (traffic goes out on the real interface)` |
| 12:31:01 | `waiting 9s for the old registration to clear` |
| 12:31:10 | `registration new: ok (attempt 1, cycle 1)` |
| 12:31:12 | `kill-switch re-installed` / `rotation done in 16.76s` |
| — | `/64`: `2a09:bac5:b043:1caa` → `2a09:bac5:b043:119` |

**19 s from the wall line to a fresh working block.** Nobody typed anything.

## A rotation that had to reject a spent block

Four minutes later, Cloudflare handed back a `/64` this machine had already
burned. The tool caught it and asked for another one:

```
12:25:05 WARN  quota wall detected (Free usage limit reached)
12:25:07 INFO  rotation start (quota-wall); current egress=2a09:bac5:b043:1caa::2db:c9
12:25:24 WARN  Cloudflare handed back a /64 we already exhausted (2a09:bac1:76c0:dba8) — asking for another one
12:25:39 INFO  registration new: ok (attempt 1, cycle 2)
12:25:41 INFO  rotation done in 33.61s: 2a09:bac5:b043:1caa -> 2a09:bac1:7680:dba8
```

## The honest end state: the pool runs dry

After ~20 rotations in one day, every `/64` Cloudflare returned for this device
was already exhausted:

```
12:41:16 WARN  Cloudflare handed back a /64 we already exhausted (2a09:bac1:7680:dba8) — asking for another one
12:41:33 WARN  Cloudflare handed back a /64 we already exhausted (2a09:bac1:7680:dba8) — keeping it; the free quota
               for that block is already spent, so you may still be rate-limited until Cloudflare's window resets
12:41:33 INFO  rotation done in 33.59s: 2a09:bac5:b045:1541 -> 2a09:bac1:7680:dba8
```

`ouc status` in that state:

```
✓ supervisor: running (pid 18978)
✓ warp tunnel: Status update: Connected
✓ egress: 2a09:bac1:7680:dba8::57b:3   block: 2a09:bac1:7680:dba8
✓ kill-switch: installed (as recorded by the supervisor — run with sudo to verify)
✓ rotations: 20   blacklisted blocks: 10
```

Internet works, tunnel works, quota does not. That is the truth, and the tool
tells it.

---

## Bugs this real test found (all fixed in 2.0.1)

1. **`ouc status` lied to normal users.** Run without root, the `iptables -C`
   check fails, and the failure was rendered as `kill-switch: NOT installed`
   even though the guard was armed. Fixed: the supervisor records
   `guard_installed` in `state.json`; an unprivileged `status` reports that
   record and labels it; `sudo ouc status` still asks the kernel.
2. **The `/64` blacklist dropped its own entries.** Blocks were stored as bare
   strings (`2a09:bac5:b045:1caa`) but re-validated through `norm64()`, which
   expected a full address — so on every rotation all but the newest entry
   vanished. Symptom: `rotations: 10, blacklisted blocks: 0`. Fixed in
   `norm64()`.
3. **Rotations could land you on an already-spent block.** The tool accepted any
   block different from the current one. Since Cloudflare recycles `/64`s, a
   "successful" rotation could leave you limited on the very next request. Now
   spent blocks (24 h TTL) are rejected and another one is requested, with an
   honest warning when nothing fresh is left. Covered by the new offline test
   `t23_blacklist_skips_spent_block`.

## Hard limits, stated plainly

- **Cloudflare recycles `/64`s.** Quota is per block per window. After roughly
  20 rotations in one day, this machine could not get an unspent block at all.
  Rotation then buys a working tunnel, not fresh quota.
- **opencode retries before it gives up.** A limited `opencode run` took 88–300 s
  to return even though the wall was logged within 2 s. The failing request
  stays failing; the *next* one succeeds. This delay is opencode's, not ours.
- **One rotation per cooldown.** A wall seconds after a rotation waits for
  `OUC_COOLDOWN_SECONDS` (90 s in the default install) before the next one.
- **The repair window is real.** For ~17 s per rotation the kill-switch is off
  and your real IP is visible. `SECURITY.md` §1.

---

## Reproducing it

```bash
tar xf opencode-unlimited-2.0.1.zip && cd opencode-unlimited-2.0.1
sudo bash install.sh                  # or --portable
bash tests/real-limit-test.sh 120     # real prompts until the wall, then observe
```

The driver sends real `opencode run -m opencode/flint-40b-free` prompts until
opencode logs the limit line, then reports detection latency and rotation time
from the two log files, whether the `/64` changed, and whether opencode answers
again. It never fakes a wall: if the quota is not exhausted within the request
budget, it says so and stops.

Raw evidence from the shipped test suite lives in `tests/logs/`. The live
supervisor log of this session was removed by the suite's own uninstall step
(`t34`), so the excerpts above were copied verbatim from it while the run was
in progress.
