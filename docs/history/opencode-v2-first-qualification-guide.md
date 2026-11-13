# OpenCode v2 + ouc: a beginner’s guide and qualification record

**Build:** unreleased working version 2.4.2 · **Native CLI tested:** OpenCode 2.2.7 · **Mode tested:** WARP proxy mode only.

This single guide was written **after the executed tests** described below. It explains normal use, the reliability changes, the safety incident, the corrected tests, and the remaining limits. Historical release documents are not evidence that every current v2 path works.

## 1. Read this before running anything

- **The existing installation was not replaced.** The changes belong to the review branch `fix/opencode-v2-runner-qualification`. No release, main-branch merge, permanent deployment, or new version number was authorized by this qualification.
- The installed older working copy and this candidate both say `2.4.2`. **A version number alone cannot distinguish them.** Compare the source hash.
- The candidate passed the guarded offline tests and several genuine native-CLI tests. However, the final MiniMax four-step task **did not finish**, and successful recovery from a genuine quota wall is **not qualified**.
- Recommendation: **review and controlled trials, not an unattended-production or “unlimited usage” guarantee.** Do not replace a working installation without a separate deployment decision and backup.
- Free-model availability can change during a run. A changed IP address, a model listing, or a successful request to a different model does not establish availability for your selected model.

Candidate `ouc.py` SHA-256:

```text
6be5f5d328911a5cb97c9bb5caac94eda03caff9b0d31b3e8dcf122021e09d57
```

Installed source retained during qualification:

```text
ba3320a80bdd3a528bf7ffa825bb9e7fc83214c69d075179c7e7b82a824148b0
```

Read-only checks that can be used on the existing installation:

```bash
opencode --version
ouc --version
ouc status
ouc doctor
sha256sum /usr/local/lib/opencode-unlimited/ouc.py
```

The examples using the **new candidate behavior**, including `ouc verify -m`, assume a separately approved deployment of this branch. They are not instructions to install it now. Do not run the installer merely to try a command from this guide.

## 2. What the pieces mean

**OpenCode** is the program that talks to a model and uses development tools. **ouc** is the controller around it. **WARP** provides a network connection; the HTTP bridge lets OpenCode use WARP’s SOCKS proxy.

In this tested setup:

```text
OpenCode process → HTTP proxy 127.0.0.1:8080
                 → WARP SOCKS proxy 127.0.0.1:40000
                 → provider
```

A **supervisor** is a background process that keeps the connection healthy. A **foreground run** is the command you started and are watching. A **lock** prevents two controllers from trying recovery simultaneously. A **journal** is a notebook recording task steps and outcomes.

**Headless** means a command runs without an interactive screen or a person answering prompts. A **test harness** is the code that starts a test, observes it, and checks results. A harness can have bugs too; this qualification found a consequential one, documented in section 12.

### What proxy mode does—and does not—protect

The existing ports stayed **40000 and 8080**. Qualification did not enable TUN or change firewall rules, routes, or `/etc/hosts` in the product Codespace. The existing `/usr/bin/warp-svc` was used, not replaced.

The candidate sets `HTTP_PROXY`, `HTTPS_PROXY`, `http_proxy`, and `https_proxy` consistently **for the OpenCode process tree only**. It limits both `NO_PROXY` spellings to loopback and removes inherited `ALL_PROXY` settings from that tree. There are no global shell exports in these changes.

This is **not a machine-wide VPN/privacy guarantee**. Other applications are outside the proxy policy. Arbitrary tools may not honor proxy environment variables. Routing observations are evidence about the traced calls, not a certificate covering every possible network operation.

## 3. The ordinary daily command stays simple

After an approved installation has set up the normal entry point, use:

```bash
cd /path/to/your/project
opencode
```

Do not add permanent proxy exports to your shell startup files. Do not add `--auto` to every OpenCode command. Interactive use should retain its normal approval behavior.

The candidate’s `ouc lane` is the transparent launch path. In proxy mode it execs the real CLI with the scoped proxy environment and v2 `--standalone`, so a resident server started with stale environment settings is not silently reused. It does not append advertising or plant an `AGENTS.md` rules file.

The live `lane` check used a small JSON-output request. Its output contained native JSON records only, and no `--auto` was added. A full interactive TUI session and interactive quota recovery were **not** qualified by that check.

## 4. Check the connection, then check the model

Use `ouc status` and `ouc doctor` to inspect the supervisor, WARP, and bridge. A healthy bridge is necessary, but it is not a model-availability test.

On the candidate, one genuine request to a chosen model is:

```bash
ouc verify -m opencode/beam-free
```

Replace the model ID with the exact provider/model you intend to use. This command consumes a real verification request. It does not rotate merely because verification fails.

Interpret the JSON result:

- `ok: true`: this selected model completed the exact marker reply, with a successful native exit and no typed provider error, at that time.
- `walled: true`: the native error indicated a quota wall. It is not inferred from silence or an IP change.
- `ok: false, walled: false`: another failure or an unconfirmed result. Read `failure_class` and `detail`; do not call it exhausted quota.
- A timeout is **unknown availability**, not proof of a provider-wide outage, an account limit, or a reset time.

Without `-m`, verification uses the configured test model, normally Flint in this setup. **A Flint reply does not prove MiniMax is available.** Automatic recovery now carries the interrupted model into verification instead of silently using that general probe model.

Do not use a bare inference `curl` request to decide whether the CLI is usable. Do not guess a midnight reset or wait for a fixed quota-reset hour. This implementation has no reset-wait logic.

## 5. Start a managed task carefully

The following examples describe the candidate **after an approved deployment**.

### One managed prompt

```bash
ouc run -- -m opencode/beam-free "Review this project and propose a small change."
```

`ouc run` supervises a native headless call. Its v2 output uses native JSON events, not a byte-transparent interactive display. It does **not** blanket-enable automatic tool approval.

### A task list with `ouc agent`

Use a disposable practice directory first:

```bash
work="$(mktemp -d "$HOME/ouc-practice.XXXXXX")"
cd "$work"
cat > task.txt <<'EOF'
- Create a short README.md explaining this practice project.
- Check the file and report honestly what you created.
EOF
```

After independently confirming your selected model is available:

```bash
ouc agent --task task.txt --dir "$PWD" -m opencode/beam-free
```

**Important:** `ouc agent` is intentionally unattended. Native v2 calls receive `--auto`. The controller’s guardrails are **not a security sandbox** or a per-command tool-permission firewall. Use a project where you can tolerate model mistakes; do not give it production credentials, valuable unrelated files, or an irreversible task.

A plain practice directory is not a Git repository, so Git checkpoints are skipped. In an existing repository, use a clean dedicated branch/worktree first. The checkpoint helper stages project changes with `git add -A`; unrelated dirty files in that worktree could be included. It does not push those checkpoints.

A completed native call means the controller observed completion, not that the generated code meets every requirement. Review the files and run independent checks.

## 6. Understand the notebook and continuation

Normal user-state files are under:

```text
~/.local/state/opencode-unlimited/
  journal.json
  journal.md
  run.json
  run-report-<timestamp>.md
```

The journal is bound to the task text and work directory. A different task/directory archives the previous notebook rather than reporting an unrelated task as already done. Older journals without binding metadata have a compatibility migration; do not assume that migration proves task identity.

- `journal.md` is the readable notebook.
- `run.json` gives current counters and step status.
- The report explains what finished, what did not, and why the command stopped.
- An interrupted step may be retried. **This is not exactly-once execution of tools.** Check partial work before retrying a non-idempotent operation.
- Do not use `--fresh` as a casual repair command: it starts a fresh notebook instead of resuming the current one. Back up important journals first.

The v2 launcher makes actual `cwd` and the child’s `PWD` agree, including symlink resolution. This matters because a stale `PWD` can send shell tools to the wrong project.

For an explicit native continuation, retain **all three**: session ID, model, and project directory:

```bash
cd /the/same/project
ouc resume -s ACTUAL_SESSION_ID -m opencode/beam-free
```

`ACTUAL_SESSION_ID` is a placeholder, not a value to paste literally. Use the ID recorded for your real invocation. In v2, a nonexistent `-s` ID may create a session; it does not reliably produce a “not found” error. Never invent an ID or silently substitute the most recent conversation.

The candidate uses a known v2 ID exactly, unless an explicit continuation preference was configured. If it cannot identify the interrupted v2 model for recovery, it stops and asks for model selection rather than pretending a different model verifies it.

The live continuation test stored a random nonsensitive token, then requested it in the **same exact session** without repeating the token in the continuation prompt. It retained the selected model and directory. That proves manual session continuity for the tested CLI, **not automatic recovery from a real quota wall**.

## 7. Different failures need different actions

| Observed condition | Candidate behavior |
|---|---|
| Typed provider free-limit error | Verify recovery using the interrupted model; rotate/retry only through the recovery path and its budgets |
| RPM/ordinary rate error | Bounded backoff; do not spend blocks or switch model |
| Network/transport error | Connectivity retries; no quota claim and no block consumption |
| Authentication, invalid model, or configuration error | Actionable stop rather than an endless retry |
| Unknown typed error or unexplained exit | Preserve evidence; bounded step attempts, not silent success |
| No meaningful native progress | Stop owned call, retry once, then record failure |
| User cancellation | Stop owned children, release locks, record cancellation |

The parser distinguishes native top-level provider/control errors from ordinary text and tool output. A tool can legitimately print `429K`, SVG coordinates containing `429`, or a quoted error example. Those are not automatically provider rate/quota failures. A tool’s own `status: error` is not a provider-error event.

An exit code of zero is insufficient: a typed error still fails; a truncated v2 stream without a completed step fails; a verification marker plus a typed error fails.

The last MiniMax task produced a genuine `provider.transport` error containing `ECONNRESET`. Replaying that preserved record through the final classifier returned **network**, not quota. The external qualification observer cancelled the live phase conservatively; the normal network-retry sequence therefore was not exercised to completion in that live task.

## 8. Defaults, progress, cancellation, and ownership

Approved product defaults were retained:

| Setting | Value |
|---|---:|
| Minimum rotation spacing | 15 seconds + 0–5 seconds jitter |
| Rotations per hour | Unlimited count (`0`) |
| Rotations per run | Unlimited count (`0`) |
| Automatic resume count | Unlimited (`0`) |
| Consecutive-failure limit | 60 |
| One run’s time budget | 10 hours |
| Fixed quota-reset waiting | None |

Unlimited **counts** do not mean unlimited provider capacity or an endless successful run. Other time/verification/failure limits still apply. The ten-hour default was checked, not demonstrated by a ten-hour live soak. Restarting the agent starts a new invocation clock while retaining notebook history/counters.

Meaningful progress is based on native events, not an unrelated busy process’s CPU usage. The default inactivity threshold is ten minutes. Long blocking work can still be difficult to judge; inspect the report rather than assuming every silence means quota.

Active elapsed limits use a monotonic clock, which does not jump when the machine clock changes. Persistent audit timestamps, blacklist ages, rotation stamps, and cross-process heartbeat timestamps remain wall-clock values; they were not blindly converted.

Use Ctrl+C to cancel a managed command. Typical cancellation exits are `130` for interrupt and `143` for termination. The controller tracks owned processes using Linux process identities/start times. It cleans descendants on cancellation/stall/deadline and after the native CLI exits; it does not use a broad name-based kill of unrelated model calls.

A managed run holds both its foreground lease and the physical `rotation.lock`. Its own rotation re-enters that lock; an older supervisor that only understands that lock cannot rotate concurrently. Existing rotation-lock permissions are preserved.

**Advanced warning:** copying `OUC_STATE_DIR` creates a different lock namespace unless the physical rotation lock is deliberately shared. `OUC_USER_STATE_DIR` controls journals **and fallback request files**. Neither variable is a general security sandbox. Do not copy qualification environment settings into your everyday shell.

## 9. What was actually tested

Machine: existing GitHub Codespace, real OpenCode 2.2.7, existing WARP/HTTP bridge, proxy mode. The candidate was staged separately, never installed over the working copy.

### Offline results

- **70 focused runner tests passed locally**; they cover native events, models/sessions/cwd, proxy environment, content guards, recovery simulations, locks, clock jumps, cancellation, and cleanup.
- **70/70 passed on the Codespace under external tracing**, with **zero Internet-family `connect()` attempts**, unchanged real shared-channel hashes, no global fallback request, and installed rotation counter **94 → 94**.
- The **final safe-default suite: 55 groups passed, 0 failed, 11 skipped**. One of those groups contains the 70 focused tests; do not add the counts as independent groups.
- The 11 skips are **five legacy installer/uninstaller groups** and **six destructive real-WARP groups**. Their absence is intentional, not a hidden pass.
- Before the additional installer safety gate, the same product source also completed **60 groups / 0 failures / 6 excluded real-WARP groups** in the disposable local agent sandbox. This was not an install/uninstall test on the valuable product Codespace.
- Python compilation, Bash syntax, diff-whitespace checks, and the selected Ruff undefined-name/syntax checks passed. The previously unreferenced `doctor_proxy` logger error was also fixed and tested.

Quota, rate, network, session-error, and cancellation fixtures are explicitly **simulations**, not live quota-recovery evidence. Additional offline replay classified 21 preserved genuine free-limit records as quota and one genuine transport record as network; replay does not make another provider request.

### Genuine final-source CLI results

| Check | Measured result |
|---|---|
| MiniMax selected-model verification | Pass, 8.408 seconds |
| MiniMax fresh four-step website task | **Not completed**; external safety cancellation after a native transport reset, 378.494 seconds, exit 143 |
| Separate Beam fresh four-step website task | **4/4 done**, 337.373 seconds, four native calls, exit 0 |
| Actual shell `pwd` through `ouc agent` | Pass, 7.088 seconds |
| Native memory setup | Pass, 8.082 seconds |
| Explicit-session resume, including same-model verification | Pass, 11.155 seconds, two native calls |
| `lane` native output, no added `--auto` | Pass, 5.077 seconds |
| Benign model text `429K` | Pass, 5.938 seconds |
| Invalid model | Actual native fatal error, one attempt, exit 1, no rotation |
| Process-only closed proxy | Expected unsuccessful timeout; **not** classified as quota |
| Final configured-model verification | Genuine successful Flint marker reply |

The MiniMax failure was **not a quota observation** and was not a natural completion/retry outcome: the external safety observer deliberately cancelled at the provider error. Earlier candidate snapshots had completed MiniMax and Beam tasks; those records remain history, not proof that the final MiniMax run passed.

No CLI/classifier wrapper substituted model behavior in the live qualification. The external observer inspected process/connect/exec evidence and could cancel, but could not manufacture a successful reply. Native config/data/state/cache were scoped to a copied private XDG profile; controller journals were also isolated. The physical rotation fence was independently observed during live managed calls.

Across the 12 native invocations in the main corrected qualification phases, selected models, physical cwd/PWD, JSON mode, and bridge connections were observed. Destination review found no nonloopback `connect()` attempt in those traces and no WARP rotation/registration mutation command. Connection counts include internal/tool activity and are **not LLM-request counts**. Invalid-model, closed-proxy, and final-health calls are recorded separately.

**Successful automatic recovery from a genuine quota wall remains unproven.** So do ten-hour unattended operation, production TUN/firewall mode, and a complete final-source MiniMax task.

## 10. Independent website checks—not just the model’s self-report

The final Beam files were copied without editing and hash-checked against the remote outputs:

| File | Bytes |
|---|---:|
| `index.html` | 28,286 |
| `about.html` | 23,948 |
| `styles.css` | 3,070 |

Independent Chromium checks covered both pages at 1440 and 390 pixels, in light and dark mode: **8 cases**, all HTTP 200, no horizontal overflow, no page-JavaScript errors, and no failed requests in that audit. Both pages linked the official Tailwind CDN and `styles.css`. Each had four desktop content-navigation links, excluding logo and CTA. Theme toggles, mobile-menu expansion, and the About FAQ interaction worked.

Four desktop axe audits reported **zero confirmed violations**, but retained unresolved color-contrast checks: 8, 9, 7, and 8 nodes respectively. Mobile axe audits were not run. This is **not a WCAG certificate** or proof that every focus/contrast condition is correct.

Caveats retained rather than repaired behind the model’s back:

- Both pages had a recoverable HTML entity parse warning in the Google Fonts URL (`&display`); an external font is also a network dependency.
- Some navigation destinations are demo placeholders. There is no real networking backend, authentication, billing, or product service.
- The custom CSS had the requested gradient, smooth-scroll, hover-lift, and reduced-motion features. Its lack of a standalone `:focus` rule is not by itself proof of missing focus styles, because HTML Tailwind classes can supply them.
- Tailwind was served from the same previously cached official script for the browser audit. The audit does not establish current CDN availability on every network.

This was compatibility/task qualification, **not a new model speed ranking**. It would be misleading to compare a stopped MiniMax task with Beam’s completed task as a performance benchmark.

## 11. Run the safe tests yourself

For the guarded native-runner simulations, no provider account, WARP service, or GitHub device login is needed:

```bash
python3 tests/test_runner_v2.py -v
```

The safe-default compatibility run is:

```bash
OUC_TEST_REAL=0 bash tests/run_tests.sh --offline
```

The legacy compatibility script may re-exec through passwordless sudo for sandbox permission checks. Review it first and prefer a disposable development environment. Do not use it as a diagnostic command against an important installation.

All focused fixture paths are checked before controller writes. Their fake executables require explicit test-owned state paths. `--version` remains side-effect-free. The legacy harness now explicitly scopes user state, fallback requests, logs, and helper state; cleanup targets the current sandbox rather than every similarly named sandbox.

Five legacy installer/uninstaller tests still have branded host-system effects, so they are **excluded by default** and require an explicit disposable-host opt-in. The six legacy real-WARP tests require additional explicit destructive-test opt-ins. The valuable product Codespace did not run these groups. Do not turn these flags on there. Older documentation showing an unconditional “run everything” command is historical, not the recommended workflow.

The live observer is separate from these offline simulations. Its conservative stop policy is an experimental safety boundary, not a changed product default.

## 12. The safety incident and its correction

An earlier new regression test failed to override `OUC_USER_STATE_DIR`. Its primary test files were temporary, but its fallback request resolved to the **real** `/tmp/opencode-unlimited/hop.request`.

That harness wrote exactly `{"id":"fallback_fixture","ts":2}`. The installed older supervisor alternated that dummy request with an already acknowledged historical request, repeatedly performing manual hops. This was **a mistake in the qualification harness**, not evidence of a provider-wide outage.

Consequences preserved in the record:

- The contaminated candidate run observed 21 genuine native free-limit errors and recorded 20 recovery rotations/resumes, without completing the first step.
- Its copied controller counter moved **43 → 63**.
- The installed supervisor’s counter moved **43 → 94** while the fallback replay continued.
- These counters are not unique IP/block counts. Neither candidate verification with Flint nor an egress change proved recovery for MiniMax.
- That run is **invalid qualification evidence**, not a successful recovery test.

The exact test-owned fallback file was identified against the original baseline, moved out of the active channel, and retained privately as evidence. The qualification controller was cancelled by its exact process identity. Existing log history was acknowledged through EOF and the pending replay flag cleared; counters, blacklist history, and WARP registration history were **not rolled back**. No real original request was deleted.

While the old supervisor was registering during the incident, proxy health was temporarily poor; it was not a daemon crash or proof that general Codespace Internet access was lost. After quarantining the fixture, the existing runtime returned healthy without a reinstall. The user was informed and explicitly approved the safer continuation.

Corrections then included:

1. Full focused-fixture path assertions, including fallback channels, and fail-closed fake helper paths.
2. External before/after checks showing the repaired focused tests do not touch real channels or make Internet connections.
3. A foreground fence on the shared physical rotation lock, compatible with the older supervisor’s flock interface.
4. Verification of the interrupted model, with no silent default-model substitution or saved-default modification.
5. A separate native XDG profile and a conservative independent live safety observer.
6. Explicit exclusions for legacy installer/destructive test groups that are not safe on a valuable host.

There was also an earlier observer-only mistake: it expected the native tool to be named `bash`, while OpenCode emitted `shell` for a successful `pwd`. The original failure was retained and corrected using the exact recorded command/output, not fabricated evidence or overwritten logs.

## 13. Final protection checks, evidence, and readiness

Final checks found:

- Protected-path signatures and the extra baseline content/mode checks unchanged.
- **3,653 existing OpenCode configuration files** matched the original private backup.
- **17 original notebook/report files**, including the original `journal.json`, `journal.md`, and `run.json`, matched the backup.
- Original repository HEAD and dirty working-tree status were preserved.
- The installed source remained the old hash shown in section 1.
- Supervisor alive, WARP connected, ports 40000/8080 open, `doctor` successful, and a genuine final configured-model verification successful.
- No test-owned native process remained.
- Corrected-run installed hop counter **94 → 94**, with the global fallback request absent.
- No recoverable-cache cleanup or important-file deletion was used to obtain these results. The product Codespace disk check ended around **16 GB used / 15 GB available (52%)**; these are rounded filesystem measurements.

Do not summarize this as “all state unchanged”: operational rotation/history state changed during the incident and was deliberately preserved. Do not summarize it as “all live tests passed”: the final MiniMax full task did not finish.

Public, deliberately limited evidence is in [`tests/qualification/`](tests/qualification/). Raw traces, native logs, private backups, and the stopped-run evidence remain in the qualification workspace. Private archives may contain registration/configuration information and must not be committed or shared as ordinary test output.

Machine logs label their wall-clock timestamps `2026-11-12`, whereas the session’s supplied date was `2026-11-08`. Those observed timestamps are retained without treating them as an authoritative calendar date. Durations above use monotonic measurement; no quota/reset conclusion is based on that clock discrepancy.

**Delivery decision:** commit/push the reviewed feature branch for inspection, keeping this qualification record and its limits. Do not publish a release or deploy over the working installation under this approval. A later deployment should back up source/config/state first, keep proxy mode and ports unchanged, use the existing `/usr/bin/warp-svc`, and stop if health or protected-file checks differ.

**Before a production/unattended-readiness claim:** complete a final-source MiniMax task under an agreed bounded retry policy, qualify genuine quota recovery with the same model when naturally encountered, and perform an appropriately supervised long-duration test. Do not deliberately exhaust free usage to manufacture that evidence.
