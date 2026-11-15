# OpenCode + ouc: the current beginner guide

**Controller:** v2.6.5 · **Engines:** WARP (primary) + Psiphon (automatic fallback) · **Modes:** tunnel mode and proxy mode.

This guide was updated **after the executed follow-up tests, complete MiniMax task, installed Codespace update, and installed smoke tests**. For the detailed chronology, qualifications, and unsuccessful attempts, read the [English execution report](docs/reports/transport-resilience-follow-up.md). The [previous guide](docs/history/opencode-v2-first-qualification-guide.md) is preserved unchanged as historical evidence.

> **The one new idea in v2.6.5, in plain English.** The tool has *two* engines
> now. **WARP** is the first choice — it gives you a fresh address by rotating
> to a new IPv6 block. But sometimes WARP itself is blocked (the provider
> refuses every WARP address). When that happens, the tool automatically
> switches to **Psiphon**, a completely separate engine, and keeps your
> session going. You do not have to do anything: it notices WARP is failing,
> hands over to Psiphon, and hands back when WARP recovers. `ouc status`
> shows which engine is active right now.

## 1. The simple daily command

Open a **new Codespace terminal**, enter your own project, and run:

```bash
opencode
```

That remains the ordinary interactive command. You do not need to export proxy settings or paste a long command each day.

The approved update added one previously absent file, `~/.bash_aliases`, defining an `opencode` Bash function. It forwards all native arguments to the installed `lane` execution path, including leading `--version` and `--help` flags, and preserves terminal input. The existing `.bashrc` already loads that file. Neither `.bashrc` nor the real OpenCode binary was replaced. If you must keep an already-open Bash terminal, load the new entry once:

```bash
source ~/.bash_aliases
```

Other shells and non-interactive scripts do not necessarily load this Bash function. Managed automation can use `ouc run`; a manual `ouc lane run ...` also accepts a positional native subcommand. The direct `ouc lane --version` spelling still has the old leading-option parser limitation; the daily function avoids it without changing the qualified controller. Do not rename the native binary or stack another wrapper on top of it. The exact already-installed integration can be reviewed in [the Bash entry example](docs/examples/codespace-opencode-entry.bash); it is not an instruction to overwrite an existing user aliases file.

**Important distinction:** the daily shortcut provides transparent native execution through the process-scoped proxy. It does not turn the interactive screen into a headless managed task. The new controller retry state machine is used by `ouc agent`, `ouc run`, and `ouc resume`.

## 2. What the pieces mean

- **OpenCode** talks to a model and uses development tools.
- **ouc** supervises managed calls, records task progress, and coordinates network recovery.
- **WARP** is the primary engine. It supplies the connection and can rotate to a fresh IPv6 block when your quota runs out.
- **Psiphon** is the fallback engine. If WARP is blocked, ouc switches to Psiphon automatically and keeps the same session alive. You can also force it with `ouc hop --backend psiphon`.
- The **HTTP bridge** adapts OpenCode's HTTP-proxy support to whichever engine's SOCKS proxy is active (WARP on 40000, Psiphon on 9060), so OpenCode always talks to one local address.
- A **supervisor** is a background process keeping runtime components healthy.
- A **session** is a particular model conversation. A model name, project directory, and session ID are different pieces of context.
- A **journal** is a durable notebook of steps, outcomes, and budgets. A **lock** prevents competing controllers from recovering the same connection simultaneously.
- **Headless** means running without an interactive screen or a person answering prompts.
- A **harness** is test code that starts, observes, and checks another program. It can have its own bugs.
- A **fixture** deliberately fakes provider/WARP results. A **monkey test** randomizes inputs; a **stress test** adds concurrency/load. Neither automatically proves a genuine provider outage was recovered from.

The tested connection is:

```text
OpenCode process tree
  → HTTP proxy 127.0.0.1:8080
  → WARP SOCKS proxy 127.0.0.1:40000
  → provider
```

Ports **40000 and 8080** were retained. This update did not enable TUN or change firewall rules, routes, or hosts. It reused the existing `/usr/bin/warp-svc` and registration.

Proxy settings are applied to the OpenCode subprocess tree only, not globally to your shell. This is **not a machine-wide VPN or firewall guarantee**. Arbitrary browser/tool subprocesses may ignore environment proxies. Failed direct reachability probes must be counted honestly rather than confused with successful provider connections.

## 3. Check the installed build

```bash
ouc --version     # → 2.6.5
ouc status        # which engine is active, is the supervisor alive
ouc doctor        # checks BOTH engines: WARP and Psiphon
sha256sum /usr/local/lib/opencode-unlimited/ouc.py
```

`ouc doctor` prints a line for each engine. You want to see:

```text
  ✓ psiphon fallback: ready (/usr/local/bin/psiphon-tunnel-core)
```

If Psiphon is missing it tells you the exact fix
(`sudo bash install.sh --with-psiphon`). `status` and `doctor` check
runtime/connection conditions; they do not establish that every free model is
currently available. A model listing, changed IP, successful curl, or
successful different model is not proof that your chosen model can complete
its task.

An explicit model verification sends a real small provider request:

```bash
ouc verify -m opencode/minimax-m3.1-flash-free
```

Availability can change after that check. The verified model must be the interrupted model when deciding whether its recovery worked.

## 4. Run a multi-step task with controller recovery

Create your own task file first. Use a short list of clear steps, one per line beginning with `-`. Keep it in a project you intend the agent to edit. For example, the contents could ask for a page, a stylesheet, then verification of those files.

From that project, replace `my-task.txt` with your actual task filename:

```bash
ouc agent --task ./my-task.txt --dir "$PWD" -m opencode/minimax-m3.1-flash-free
```

`--task` names the instructions file; `--dir` is the work directory; `-m` selects the task model. A new task/directory pair gets its own journal instead of inheriting an unrelated completed task.

Managed `agent` calls deliberately approve development-tool use. That is not an OS sandbox or permission to delete unrelated data. The controller has guardrails and protected-state audits, but they do not make arbitrary model-generated commands harmless. Keep your own important project backups too.

A small managed native call can use:

```bash
ouc run -- -m opencode/minimax-m3.1-flash-free "Reply with exactly HELLO. Do not use tools."
```

The `--` separates ouc's arguments from the native run arguments. Managed v2 output includes native JSON events and controller progress. That structured output is intentional; it is not the interactive TUI. `ouc run` and ordinary `lane` do not silently add `--auto`.

## 5. Resume the right conversation

For an explicit continuation, enter the **same project** and supply the actual recorded session and model:

```bash
ouc resume -s ACTUAL_RECORDED_SESSION_ID -m opencode/minimax-m3.1-flash-free
```

Do not paste the placeholder literally. Do not invent an ID: native v2 may create an unknown requested ID rather than reject it. Do not replace a known conversation with “whatever session was latest.”

The transport-recovery path uses a known session exactly, keeps the original primary model, and keeps the physical cwd/PWD aligned. If it cannot identify the primary model for recovery, it asks for explicit selection rather than verifying a different model and claiming success.

A new invocation restarts its active monotonic time budget; durable task history/counters remain in its notebook. Re-running a command is not proof that previous failed work was completed.

## 6. How transport recovery works now

A top-level native `provider.transport` / `ECONNRESET` is not automatically quota. The controller now does this:

1. The initial primary call fails with a transport/network classification.
2. Retry the **same primary task**, up to **three additional attempts**.
3. If still failing, start a separate secondary-model diagnostic with a fixed marker prompt and no tools/task session.
4. If the secondary answers, retry the primary once more. Secondary success alone cannot mark the task done.
5. If the secondary fails, or the primary still fails after that check, run bounded rotation and verify the original primary model on the resulting route.
6. Continue only the primary task, subject to its remaining budgets. An unverifiable route is not a successful recovery.

Authentication, invalid model/configuration, exhausted time/failure budgets, and cancellation can stop the sequence earlier. Native OpenCode also retries requests internally: three additional **controller invocations** is not a total limit of three provider HTTP requests.

A diagnostic uses temporary HOME/XDG storage, an empty directory, a fixed prompt, and native deny permissions/policies. It receives no copied task prompt, task session, task configuration, or provider credentials, and no `--auto`. Tools/MCP/plugins/skills/web search are denied; any tool event invalidates its result. This is enforceable native permission configuration, not a prompt-only request to “be careful,” but it is still not an OS sandbox.

The diagnostic model defaults to the existing test-model setting—Flint in this installation—and selects a distinct alternative if that would be the primary itself. It never changes the task model. The selected secondary's availability is not guaranteed; live secondary checks during qualification returned typed quota errors, which are retained as failures.

Invocation-only tuning is available through `OUC_TRANSPORT_RETRIES`, `OUC_TRANSPORT_RETRY_DELAY`, `OUC_TRANSPORT_DIAGNOSTIC_MODEL`, and `OUC_TRANSPORT_DIAGNOSTIC_TIMEOUT`. Defaults are three retries, a two-second base retry delay, and a 25-second diagnostic timeout. Do not globally export qualification environments or copy a test profile into your everyday shell.

## 6b. The fallback ladder: when WARP itself is blocked

Rotating WARP blocks only helps if WARP is reachable. If the provider blocks
*every* WARP address, no amount of rotating will help. That is when the
second engine takes over:

1. The supervisor counts consecutive WARP failures. At **5** (the default),
   it decides WARP is walled, not just unlucky.
2. It **escalates to Psiphon**: it brings WARP's tunnel down, switches the
   firewall guard to its proxy dialect, starts Psiphon, and points the bridge
   at Psiphon's SOCKS port. Your session is not dropped.
3. It verifies Psiphon with a real request before trusting it.
4. When WARP recovers, it hands back automatically and re-arms the tunnel
   guard and the IPv6 pin.

You can watch this happen in `ouc status` (it shows the active engine) and in
the log (`tail -f /var/lib/opencode-unlimited/ouc.log`). To force Psiphon
right now, run `ouc hop --backend psiphon`. To check both engines are ready,
run `ouc doctor`.

## 7. Different failures mean different actions

| Observation | Correct interpretation/action |
|---|---|
| Typed provider free-limit | Quota path; same-model verification and bounded recovery |
| Ordinary rate/RPM limit | Bounded backoff; not evidence of a spent block |
| Transport/network failure | Three additional primary attempts, isolated diagnosis, then primary-only recovery |
| Authentication or unavailable/invalid model | Actionable stop; not endless rotation |
| Tool error or quoted `429K` text | Not automatically a provider quota/rate error |
| Unknown error, unexplained exit, incomplete stream | Record evidence; never silently call it complete |
| No meaningful native progress | Managed stall/deadline policy cleans the owned call |
| User cancellation | Clean owned children, release locks, record interruption/cancellation |

Exit zero is insufficient if the event stream contains a typed error or never completes. A marker followed by an error is not a successful verification.

An egress probe, secondary success, or rotation success does **not** clear repeated primary task failures. Transport-only rotation does not falsely mark the old block quota-exhausted.

The bridge's former 20-second SOCKS handshake timeout no longer remains on an established response stream. Long model thinking pauses should not be cut off by that handshake timer. Managed inactivity/deadline/cancellation rules still apply.

## 8. Defaults, progress, and cancellation

The approved defaults remain:

| Setting | Default |
|---|---:|
| Rotation spacing | 15 seconds plus 0–5 seconds jitter |
| Rotations per hour/run | Unlimited counts (`0`) |
| Resume count | Unlimited (`0`) |
| Consecutive failures | 60 |
| Run time | 10 hours |
| Fixed quota-reset waiting | None |

Unlimited counts do not create provider capacity. Verification, time, and consecutive-failure budgets still bound recovery. The ten-hour default was checked, **not proven by a ten-hour live soak**.

Meaningful progress uses native events, not unrelated CPU activity. Status exposes steps, transport retries, diagnoses, rotations, and current detail. Use Ctrl+C to cancel your managed command. Typical exits are `130` for interrupt and `143` for termination.

Cleanup tracks owned Linux process identities/start times; it does not kill every process with a matching name. A managed call holds the physical rotation lock so an older/competing supervisor cannot rotate behind it. Copying controller state without sharing that physical lock would defeat the coordination.

The final entry-point check also verified actual native `opencode --version` and `opencode --help`. The original alias-only smoke and its later leading-flag failure remain recorded separately.

`OUC_USER_STATE_DIR` scopes journals **and fallback requests**, not just display files. Neither it nor `OUC_STATE_DIR` is a security sandbox. A previous qualification fixture leaked through an unscoped global fallback channel; current fixtures fail closed without explicit private paths. Do not restore that quarantined fixture or roll back current hop history from an old archive.

## 9. What actually passed—and what did not get proven

Executed final-source evidence:

- **100 focused regressions**, locally and in the Codespace. The exact final remote tests made **zero INET connect attempts**.
- **56 safe-default compatibility groups**, zero failures; 11 destructive/host-installer groups intentionally skipped. Focused cases are included in those groups, not extra independent totals.
- **128 local** and **96 remote** randomized subprocess cases, 4,000 randomly chunked event streams per environment, and 16 lease contenders per environment. Those use fakes; no real quota was deliberately exhausted.
- A real bridge test received a response after **22 seconds** of upstream silence, longer than its former 20-second timeout.
- The unchanged MiniMax task completed **4/4** on the final source in **1,192.031 seconds**, with zero final-run transport failures, quota walls, rotations, resumes, or stalls.
- The actual installed controller performed a real `pwd`/file-creation smoke task in **15.622 seconds**. The ordinary `opencode` entry completed a separate proxy-routed native marker call in **11.482 seconds**, without added `--auto`.
- Installed `doctor` passed; ports stayed 40000/8080; hop count stayed 94. Original configuration/SSH files and notebooks matched saved hashes/modes.

The website's independent browser checks passed eight render cases, but incomplete contrast/link checks and two HTML entity warnings remain disclosed in the report. It is not an accessibility certificate.

The original observer falsely rejected failed IPv6 reachability probes after the controller finished. Its original failed result was preserved, and a separate read-only audit disclosed all 86 failed attempts and found no successful non-loopback connection. Do not summarize that as “zero attempted direct connections.”

The full transport escalation is tested with simulated faults. Two real retries were observed on an intermediate build, but the final MiniMax success did not exercise an actual final-source outage. Successful genuine quota recovery, live healthy-secondary escalation, and a ten-hour soak remain unqualified.

## 10. Backups and troubleshooting

Backups, original metadata, and private evidence remain under `/workspaces/ouc-v2-resilience/`. No credentials or raw private state archives were published in Git. The original dirty repository was preserved rather than checked out over user changes; installed code was updated from the actual fetched commit.

If something is wrong, inspect `ouc status`, `ouc doctor`, the recorded model/session/directory, and the run journal before changing anything. Do not immediately reinstall WARP, delete registration, flush iptables, edit hosts, or restore an entire old state archive. The current proxy-safe code prevents firewall/hosts mutations on proxy shutdown.

A rollback should coordinate active work and restore only the backed-up installed source with its metadata; the new Bash integration file can be removed only if unchanged. Old state archives must not overwrite newer history/counters. The old source loses these fixes, so rollback is a controlled maintenance action, not the first everyday command.

For reproducible test commands and explicit simulation boundaries, see [transport testing](tests/TRANSPORT_TESTING.md). For exact hashes, successful and unsuccessful phases, deployment evidence, and limitations, see [public results](tests/qualification/transport-results.json) and the [execution report](docs/reports/transport-resilience-follow-up.md).
