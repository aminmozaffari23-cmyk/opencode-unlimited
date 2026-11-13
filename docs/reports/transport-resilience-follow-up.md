# OpenCode v2 transport resilience: execution and deployment report

## 1. Outcome and evidence limits

**The requested controller recovery is implemented, committed, pushed, installed in the Codespace, and smoke-tested. The unchanged MiniMax website task completed all four steps on the final source.**

The policy is now:

> Initial primary-model transport failure → three additional primary attempts → isolated, tool-denied secondary-model diagnosis → retry the primary if the secondary answers → otherwise, or if the primary still fails, perform bounded rotation and verify the original primary model before continuing it.

This is controller behavior in `ouc agent`, `ouc run`, and `ouc resume`, not an external observer repeatedly starting an unrelated task. The secondary never takes over the task.

The final MiniMax run completed without a transport failure. Therefore, **successful automatic recovery from a genuine final-source transport outage was not observed in that run**. The complete escalation sequences were exercised with fault-injected offline subprocesses. Two actual controller retries after genuine transport errors were observed on an intermediate build, but that interrupted intermediate run is not presented as a completed recovery. A successful genuine quota-wall recovery and a ten-hour live soak also remain unproven.

This is a substantial resilience improvement and a qualified controlled deployment—not a promise of unlimited provider capacity or unattended production reliability under every failure.

## 2. Exact build and delivery boundary

| Item | Recorded value |
|---|---|
| Review branch | `feat/transport-retry-diagnostics` |
| Implementation commit | `89ffea83531d68ed309439037d85890b62abaa34` |
| Previous qualification/base commit | `608a9f812b148a83d9fd52731662554ce609251d` |
| Main branch, unchanged | `0ee0825cb69ad0286d9f97e4e1560524553051e1` |
| Controller version string | Unreleased `2.4.2`, unchanged |
| Native CLI | OpenCode `2.2.7`, binary not replaced |
| Product networking | WARP proxy mode; SOCKS `40000`, HTTP bridge `8080` |

**Final candidate and installed `ouc.py` SHA-256:**

```text
e4b1b802a2697b7cc26f985fc1549549efbc24d40523f2ff9b52c101a1f0bfe2
```

**Previous installed source SHA-256:**

```text
ba3320a80bdd3a528bf7ffa825bb9e7fc83214c69d075179c7e7b82a824148b0
```

The actual GitHub remote was checked, not assumed from an old checkout. The feature branch push was verified with `ls-remote`; the Codespace fetched the branch from the same origin and extracted the installation payload from the recorded commit. Source hashes matched. No main merge, release publication, published ZIP replacement, or new version bump was performed.

Public review entry points:

- [Implementation commit](https://github.com/aminmozaffari23-cmyk/opencode-unlimited/commit/89ffea83531d68ed309439037d85890b62abaa34)
- [Feature branch](https://github.com/aminmozaffari23-cmyk/opencode-unlimited/tree/feat/transport-retry-diagnostics)
- [Public result record](https://github.com/aminmozaffari23-cmyk/opencode-unlimited/blob/feat/transport-retry-diagnostics/tests/qualification/transport-results.json)
- [Current beginner guide](https://github.com/aminmozaffari23-cmyk/opencode-unlimited/blob/feat/transport-retry-diagnostics/BEGINNER_GUIDE.md)

Version `2.4.2` alone cannot identify this build: the older installed file used that string too.

## 3. Fresh baseline and backups

Before this follow-up, a new private qualification workspace and isolated Git branch were created. The existing 70 focused regressions passed in **4.523 seconds**.

The Codespace baseline found something different from the previous session's last healthy observation: **the runtime was down**. Neither proxy port was listening; WARP's CLI could not reach its daemon; the old supervisor PID had no live process. The Codespace API reporting “Available” did not establish application health.

Fresh remote backups were retained:

- Repository archive: **1,018,713 bytes**.
- Installed code/configuration/state/WARP archive: **14,413,028 bytes**, private and permission-restricted.
- Original Git HEAD and dirty status, protected content hashes/modes, notebook hashes, request cursors, and disk usage were recorded separately.

The existing `/usr/bin/warp-svc` was started and the existing registration reconnected. WARP was **not reinstalled**, and a new registration was not created for this startup. The daemon remained PID **1754** through qualification and deployment. An HTTP bridge was started without a competing old supervisor.

The local baseline SSH call timed out while waiting, but inspection confirmed that its remote backup operation had completed. It was not blindly rerun over the original evidence directory.

## 4. What the controller now does

### Primary context and escalation

`TransportRecovery` implements the shared transport policy. Its three retries are **three additional native primary invocations after the initial failed invocation**, not three total calls. Native OpenCode also has its own internal request retry loop; four native invocations are not necessarily four HTTP requests or four model turns.

The controller retains the selected primary model and physical project directory. A known transport-recovery session is resumed exactly, rather than silently selecting an unrelated latest conversation. An unknown primary model is not replaced with the diagnostic model.

After those retries:

1. A secondary check receives a fixed marker prompt only.
2. A healthy secondary permits one more primary task call; it does not establish that the primary recovered.
3. A failed secondary, or another primary transport failure after a healthy check, enters the bounded rotation path.
4. Rotation must be verified with the **original primary model**, even if ordinary verification was disabled. A changed IP alone is insufficient.
5. The resumed primary task must still finish successfully.

A transport-only rotation does not label the old block quota-exhausted. Ordinary rate limits remain backoff events; authentication/configuration/invalid-model failures stop actionably; a typed provider free-limit remains a separate quota classification.

### No false clearing of failures

The agent previously cleared consecutive failures when an egress probe worked. That was not a successful provider task reply. Egress availability, secondary success, and rotation success now do **not** clear the primary task's consecutive-failure counter. The quota path also no longer clears repeated task failures merely because a block verification passed.

Deadlines, configured failure/resume/rotation limits, and cancellation still bound escalation. Transport escalation uses a bounded rotation/verification call; the underlying rotation algorithm may examine more than one candidate. It is not a promise of exactly one WARP API operation.

### Diagnostic isolation and a real bug discovered during qualification

The diagnostic has a separate temporary HOME, XDG directories, and empty working directory. It receives no task prompt, task session, copied provider credentials, or task configuration override. It has no `-s`, `-c`, or `--auto`. Native v2 policies deny tools, MCP, skills, plugins, and web search, while wildcard permissions deny tool actions. Any unexpected tool event invalidates the diagnostic. Its private profile is cleaned afterward.

An initial implementation also denied integration routing. Real native testing showed that this disabled the anonymous provider route itself and misleadingly produced “Model unavailable.” That restriction was corrected without lifting the tool restrictions. Native `debug config` and `debug agents` confirmed that the deny rules were loaded.

Actual isolated Flint, Beam, and DeepSeek component checks then returned genuine typed free-limit errors, not successful diagnostic markers. These failures were retained and correctly classified. They do not prove that MiniMax, all providers, or all egresses were exhausted: MiniMax verification and the website task succeeded. The live secondary-success branch was not proven; its behavior is covered by the simulated regressions.

This is native permission isolation, **not an operating-system security sandbox**.

## 5. The bridge defect found beneath the retry policy

Intermediate MiniMax calls repeatedly reset while reading responses. Inspection found a concrete local bridge bug: `_socks5_connect()` created a socket with a **20-second connection/handshake timeout and returned it with that timeout still active**. A model thinking or pausing long enough could make the relay shut down an established stream.

A failing regression reproduced the timeout leakage using an AF_UNIX socket pair, without a network interface or provider. The fix clears the socket timeout after the SOCKS handshake; managed task deadlines, stall detection, cancellation, and owned-process cleanup continue to govern task liveness.

A second test exercised the **actual HTTP-to-SOCKS bridge** against a loopback fake SOCKS server. The upstream stayed silent for **22 seconds**, then delivered its marker successfully. Measured duration: **22.106 seconds**. No provider request or WARP mutation was involved.

Only the bridge was deliberately restarted on the existing ports before the fresh final qualification. The WARP daemon identity, registration/configuration hashes, installed configuration, and hop count were unchanged. After deployment it was restarted once more from the permanent installed path, using the same qualified source.

This proves the timeout defect and its correction. It does not establish that every past `ECONNRESET` was caused by this one defect.

Other hardening included fail-closed classification of a bare `provider.transport` code, cleanup of a native process that hangs after emitting a terminal error, guaranteed rotation-lock release if spacing raises, recovery-deadline bounds, and closing the parent's background-resume log handle.

## 6. Executed offline, monkey, and stress tests

| Final-source check | Measured result |
|---|---|
| Focused local regressions | **100/100**, 11.684 s |
| Exact final tests in Codespace under connect tracing | **100/100**, 37.242 s; **0 INET connect attempts** |
| Safe-default compatibility suite | **56 groups passed, 0 failed, 11 deliberately skipped** |
| Local randomized subprocess stress | **128 cases**, 12 workers, 41.090 s |
| Codespace randomized subprocess stress | **96 cases**, 4 workers, 112.104 s |
| Event-stream monkey tests | **4,000 per environment**, different recorded seeds |
| Lease contention | **16 competing commands per environment**, no duplicate native task |
| Static checks | Python compilation, selected Ruff correctness checks, and Git whitespace checks passed |

The 100 focused cases are included inside two compatibility groups; these counts must not be added as if they were independent unique tests.

Randomized cases run actual controller subprocesses against explicit fake native/WARP/probe executables. An independent small policy oracle checks expected outcomes. Tests cover healthy retries at each retry position, failed diagnosis, healthy diagnosis followed by primary failure, primary verification failure, quota/rate/auth distinctions, truncated streams, failure-budget exhaustion, exact context, and diagnostics that wrongly emit tools or markers without completion.

Contention tests retained only one lease owner, cancelled that exact owner, confirmed its child was cleaned, preserved an unrelated process, and reacquired the released lease. Proxy tests supplied a fake firewall binary explicitly so that a missing installed binary could not make an unsafe implementation look safe.

The first traced Codespace stress attempt exceeded an external **35-second harness timeout** under load. Its failure was retained; an audit found no remaining scoped fake processes and no changed shared channels. The harness timeout was changed to 120 seconds with identity-scoped timeout cleanup, and the final run used four workers. The product's ten-hour default and policy assertions were not weakened.

Final traced stress had loopback health-check connections only, **zero non-loopback attempts**, zero real provider requests, and zero real WARP mutations. The earlier global-fixture leak from the previous qualification did not recur.

## 7. Complete real MiniMax task and independent browser checks

The original four-step task file was unchanged:

```text
58bff6793d5b4e88e0ac380229046ef348479679c3c5c2cb45f70c58345b008b
```

The final controller invoked `opencode/minimax-m3.1-flash-free` four times, with the expected project cwd/PWD and process-scoped proxy settings. All four native exits were zero, the controller exited zero, and the journal recorded **4/4 done**. Duration: **1,192.031 seconds**, approximately **19 minutes 52 seconds**.

| Generated file | Bytes |
|---|---:|
| `index.html` | 51,779 |
| `styles.css` | 4,298 |
| `about.html` | 46,977 |

The final run recorded **zero quota walls, rotations, resumes, transport retries, and stalls**. The physical rotation fence was independently observed. Hop counters remained **94 → 94**.

Two earlier build runs were intentionally superseded, not qualified as successes: the first was stopped after the diagnostic integration-policy defect was identified; the second showed two genuine controller retries, then was stopped to repair and restart the defective bridge. Their source hashes, cancellation reasons, failed logs, and journals remain private evidence. The final-source result is not borrowed from either earlier run.

The generated files were copied byte-for-byte for independent Chromium inspection; hashes matched and their contents were not edited. Two pages × desktop/mobile × light/dark produced **8 HTTP-200 cases**, no horizontal overflow, no JavaScript errors, and no failed requests. Actual theme toggles changed and restored rendered backgrounds; the FAQ opened and closed.

Four desktop axe runs reported **zero confirmed violations**, but contrast and link-in-text checks remained incomplete. Each HTML page had one entity-parse warning. These are disclosed rather than calling the site a WCAG-certified or flawless deliverable. Tailwind's requested CDN script was served from the retained audit cache. A missing local browser library initially prevented the independent audit from launching; local audit dependencies were installed and the audit was rerun without modifying the generated site or Codespace tools.

### Important observer correction

The original external observer exited **1 after the controller had already completed successfully**, because its blanket route assertion counted **86 IPv6 reachability attempts to `2001:4860:4860::8888`, all failing with `ENETUNREACH`**, as proxy escapes.

The original result and failure were not deleted, rewritten, or called a passing observer run. A separate **read-only adjudication** checked the saved journal, native exits, model/cwd/PWD/proxy observations, exact files, and complete connect trace. It found **zero successful non-loopback connections** and no firewall/route commands. Every exception to the loopback-only predicate was that explicit failed probe. This supports task completion and the observed provider proxy route, while preserving the original observer's failed verdict and the failed-attempt count.

Proxy environment variables are not a machine-wide leak guard. Arbitrary browser/tool subprocesses may ignore them; this report does not claim zero attempted direct connections in the real website phase.

## 8. Installed Codespace update and smoke test

Deployment used the actual fetched implementation commit, not a recollected local file. It replaced exactly the installed controller source while preserving its ownership and mode. The original repository HEAD and dirty working tree were left intact instead of overwriting user changes. The old source and installation archives remain available for rollback.

The installed bridge now runs the qualified source from its permanent path. The new supervisor was PID **26730**, the installed bridge PID **26724**, and the original WARP daemon remained PID **1754**. `ouc doctor` exited **0**; WARP was connected and ports **40000/8080** were open.

Two actual installed-source smoke phases passed:

1. **Installed `ouc agent`: 15.622 s, one native invocation.** The model executed a real shell `pwd`, created only the requested smoke file in the private smoke project, and completed its journal. The observed tool output confirmed the physical work directory.
2. **Ordinary `opencode` shortcut: 11.482 s, one native invocation.** A fresh interactive Bash shell executed `opencode run` with a harmless no-tools marker request. The native reply completed, used the proxy, and did not receive `--auto`.

Both smoke traces had zero non-loopback connect attempts and zero firewall/route commands.

The first installed smoke passed in 13.613 s / 6.124 s. A subsequent delivery check found that the initial `opencode='ouc lane'` alias rejected a leading native `--version` flag in ouc's argparse layer. Its failure was preserved. Only the newly created Bash integration was corrected: it now defines an `opencode` function that invokes the same installed `cmd_lane` execution path directly, forwarding native arguments unchanged. It uses Python `-c`, not an stdin heredoc, so terminal input is preserved. The qualified controller source/hash did not change. The exact installed Bash integration is retained in `docs/examples/codespace-opencode-entry.bash`, hash `73a5c5f4c14c13b224886faafd54391f031c9c9baff4ce43de920757527430f5`. The repeated smoke timings above are for this final integration; actual `--version` and `--help` forwarding were also verified.

The pre-existing `.bashrc` already sourced `.bash_aliases`, but `.bash_aliases` did not exist before deployment. That new, reversible file holds the function. **The original `.bashrc` and native OpenCode binary were not changed.** No proxy variables were globally exported. New terminals use the ordinary daily command:

```bash
opencode
```

`lane` is transparent native execution with scoped proxy setup. The new managed retry state machine belongs to `ouc agent/run/resume`; the alias does not turn an interactive TUI into a headless managed task.

## 9. Preservation, final health, and rollback materials

Final checks confirmed:

- **3,661 original native-configuration and SSH files** matched their saved content hashes and modes.
- **17 original notebook/report files** matched their saved hashes.
- Original Git HEAD, dirty status, installed configuration, native CLI binary, shell configuration, and other protected baseline content were preserved.
- No TUN, firewall, iptables, routes, or hosts modifications were made in the product Codespace.
- No credentials were printed or committed; Git authentication was command-scoped and used encrypted SSH stdin, without device login or persisted credential configuration.
- No qualification-owned native OpenCode process remained. The installed supervisor/bridge and existing WARP daemon were deliberately left running.
- Hops remained **94**; history and counters were not rolled back.
- Disk usage was recorded before and after; no protected-data cleanup was needed.

Standing defaults remain **15–20-second rotation spacing, unlimited rotation/resume counts, 60 consecutive failures, and a ten-hour run budget**, with no fixed quota-reset sleep.

Original installation/state archives and the backed-up installed source are private in `/workspaces/ouc-v2-resilience/`. A compressed follow-up evidence archive was retained remotely and copied to the private local evidence directory. Raw archives containing configuration/state are not public Git artifacts.

Rollback was not used. A rollback must first coordinate active tasks, use the current proxy-safe shutdown path, restore only the backed-up source with its metadata, and remove only the newly created Bash integration file if its recorded hash still matches. Do **not** blindly restore an old whole-state/WARP archive over current history. The previous source also contains the bridge timeout and proxy-shutdown hazards; rollback is not a routine troubleshooting recommendation.

## 10. Practical use and remaining limits

For ordinary interactive work, open a new Codespace terminal, enter your project, and run `opencode`. For an unattended multi-step task, use `ouc agent` with an explicit project, task file, and model. Use `ouc run` or `ouc resume -s … -m …` for managed headless calls or known-session continuation. Do not invent session IDs: native v2 may create an unknown requested ID instead of rejecting it.

The current beginner guide explains these commands and the difference between a harness, a headless call, a model task, a diagnostic, and a simulated fault. The previous guide is retained as historical evidence, not silently rewritten history.

Remaining boundaries are explicit:

- No successful genuine final-source quota-wall recovery or ten-hour live soak was established.
- The final website success did not itself exercise the new transport escalation; complete branches are demonstrated by simulated regressions, with partial real retry evidence from the intermediate build.
- The secondary models tested live returned quota errors; secondary-success availability was not demonstrated.
- Native internal retries can add latency before the controller sees a terminal error. “Three additional controller attempts” is not a limit of three total provider requests.
- Tool permission rules and post-step protection audits are not an OS sandbox or a guarantee against every possible agent action.
- Successful task generation and selected automated browser checks are not a comprehensive accessibility/security certificate or a model ranking.

**Bottom line:** the controller no longer treats the first transient transport failure as a reason for observer cancellation. The requested recovery sequence is implemented and tested, the local bridge defect that could repeatedly break long responses is fixed, MiniMax completed the unchanged task on the delivered source, and the actual installed Codespace passed a small real task plus its normal `opencode` entry-point test. The remaining provider and long-duration claims are intentionally not overstated.
