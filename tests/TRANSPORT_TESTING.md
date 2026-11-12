# Transport recovery tests: what they do and do not prove

The controller remains unreleased **2.4.2**. Use the exact source SHA-256 in
`qualification/transport-results.json`, not the version string, to identify a
qualified build. No real quota is intentionally exhausted by these tests.

## Terminology for beginners

A **headless** call runs without an interactive screen. A **harness** starts a
program, watches it, and checks its result. A **fixture** is a deliberately fake
provider or WARP executable that gives a repeatable answer. A **monkey test**
randomizes event chunks and failure sequences. A **stress test** runs many
isolated controller processes at once. None of these names means a real
provider outage or a ten-hour live soak was successfully recovered from.

## Run the focused regressions

From the repository root, as your ordinary user:

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

There are 100 tests: the original 70 native-runner regressions plus 30 transport,
diagnostic, deadline, proxy-lifecycle, failure-budget and stream-idle regressions.
The fixtures must have explicit test-owned paths; there is no global fallback.
Tests do not require a provider key, real WARP, a GitHub device login, or a real
iptables command. The firewall regression deliberately supplies a fake binary
path so that "there was no binary installed" cannot hide an unsafe call.

A native error with exit code zero is still an error. A marker followed by an
error is not success. Text such as `429K`, quoted error strings and tool errors
must not be mistaken for top-level provider quota. Diagnostics must neither use
an existing task session nor receive its prompt, cwd, config or credentials.

## Run randomized subprocess/concurrency tests

Choose a NEW evidence directory; the harness refuses to overwrite one:

```bash
python3 tests/stress_transport.py \
  --cases 128 --workers 12 --seed 20261108 \
  --evidence /tmp/ouc-transport-stress-evidence
```

The evidence includes per-case logs, invocation arguments, journal counters,
expected outcomes from an independent small policy oracle, and a JSON summary.
The program checks 4,000 randomly chunked native event streams, runs the
requested number of actual controller subprocesses against explicit fake
native/WARP/probe executables, and runs 16 competing commands against one
managed lease. It then cancels the exact owner, checks that its child is gone,
checks that an unrelated process survived, and reacquires the released lease.

The timeout of a stress **harness** is separate from the product's ten-hour
budget. The per-case external timeout is 120 seconds, with identity-scoped
cleanup; this accommodates tracing/CPU contention without letting a timed-out
fixture survive. Reducing workers changes load, not the oracle or required
behavior. No real provider call, WARP registration, hosts edit or firewall
change is made. The only network sockets are isolated loopback health listeners.

A successful isolated secondary diagnosis allows one more PRIMARY task call.
It cannot mark the task done. A failed diagnosis, or a primary still failing
after that check, requests one bounded rotation with verification using the
original primary model. Such a transport rotation does not classify the old
block as quota-exhausted. Counts/time/failure budgets still bound recovery.

## Test a long-idle bridge stream

```bash
python3 tests/bridge_idle_stream.py ouc.py /tmp/ouc-bridge-idle-result.json
```

This test starts the actual HTTP-to-SOCKS bridge against a loopback fake SOCKS
server. The upstream sends nothing for **22 seconds**, longer than the previous
20-second connect timeout, then sends a marker. Receiving it proves that the
handshake timeout no longer cuts off an established stream. Managed task
inactivity detection, deadlines, cancellation and process cleanup still apply.
This is a simulated stream test, not a real model request.

## Compatibility and real qualification

`bash tests/run_tests.sh --offline` includes the focused suites and safe-default
legacy compatibility groups. Five legacy host-installer groups and six
WARP-mutating groups are deliberately excluded unless separately opted in on a
**disposable host**. Do not turn those options on in a valuable Codespace.

The separate live qualification uses the actual unchanged controller CLI and
native OpenCode binary, with an external process/connect observer. It must let
retryable provider errors reach the product's recovery policy rather than
cancel on the first error. It must keep the physical rotation lock shared,
isolate journals/XDG data, preserve original project/config/SSH files, and
separate failed IPv6/tool probes from actual successful direct connections.
Proxy environment variables are not a machine-wide firewall or a guarantee
that arbitrary browser/tool subprocesses honor them.

The English execution report and public result JSON distinguish simulated
recovery, actual intermediate-build retries, actual final-source task completion,
installed smoke tests, unsuccessful diagnostics, and unproven long-duration or
quota-recovery claims. Raw private archives are intentionally not in Git.
