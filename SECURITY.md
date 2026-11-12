# SECURITY.md — opencode-unlimited 2.3.1

This file states exactly what this tool does to your machine, what it can
expose, and how it fails. It is written so you can decide whether the
trade-off is acceptable — not to reassure you.

---

## 1. The one thing you must know

**While a rotation is in progress, your real IP address is visible to the
network.**

That is not a bug and it cannot be avoided. To get a new IPv6 block the tool
must delete the WARP registration and create a new one, and that requires
talking to Cloudflare *outside* the tunnel. Blocking that traffic is exactly
the deadlock that made the original version cut people off from the
internet.

So the design decision is explicit:

> Your internet access always wins over your privacy.

Concretely:

| Moment | Firewall | Real IP visible? |
|---|---|---|
| Normal operation, tunnel up | `OUC_LEAKGUARD` armed | No |
| Rotation / repair in progress (~17–20 s) | rules removed first | **Yes** |
| Rotation succeeded | rules re-installed | No |
| Rotation failed, tunnel still up | rules re-installed | No |
| Rotation failed, tunnel down | rules stay removed | **Yes**, and `state.json` says `degraded: true` |

Every window where the guard is off is written to the log as a `SECURITY`
line, and `ouc status` shows a loud `DEGRADED` warning until the tunnel is
back. There is no silent leak.

If you would rather never take that window, turn the kill-switch off
entirely (`OUC_DISABLE_KILL_SWITCH=1`): the tool then never touches
`iptables`, and WARP alone decides what your IP is.

## 2. What the leak guard actually is

Two chains, one jump each. IPv4:

```
iptables -N OUC_LEAKGUARD
iptables -A OUC_LEAKGUARD -o lo -j ACCEPT                                  # loopback
iptables -A OUC_LEAKGUARD -m state --state ESTABLISHED,RELATED -j ACCEPT   # replies
iptables -A OUC_LEAKGUARD -d 10.0.0.0/8 -j ACCEPT                          # your LAN
iptables -A OUC_LEAKGUARD -d 172.16.0.0/12 -j ACCEPT
iptables -A OUC_LEAKGUARD -d 192.168.0.0/16 -j ACCEPT
iptables -A OUC_LEAKGUARD -d 162.159.0.0/16 -j ACCEPT                      # WARP control API
iptables -A OUC_LEAKGUARD -d 188.114.0.0/16 -j ACCEPT                      # WARP endpoints
iptables -A OUC_LEAKGUARD -o CloudflareWARP -j ACCEPT                      # the tunnel
iptables -A OUC_LEAKGUARD -j DROP                                          # everything else
iptables -I OUTPUT -j OUC_LEAKGUARD
```

IPv6 is the same idea with `fe80::/10` (link-local) and `2606:4700::/32`
(Cloudflare) allowed, everything else dropped.

Two honest consequences of that allow-list:

- Traffic to Cloudflare's WARP endpoints is allowed **outside** the tunnel.
  It must be — otherwise the tunnel could never be re-established. If you
  point other software at those exact ranges, it would bypass the guard.
- Link-local and RFC1918 traffic is allowed, so LAN discovery keeps working.

Nothing is inspected, redirected, modified, NATed or logged, and no other
chain or table is touched. `ouc killswitch status`,
`sudo iptables -S OUTPUT | grep OUC` and `ouc status` show exactly what is
installed.

The chain is removed by: `ouc stop`, `ouc rescue`, `ouc uninstall`, the
systemd `ExecStopPost` hook (so a crashed service cannot leave you
firewalled), the supervisor's `SIGTERM`/`SIGINT` handler, and
`uninstall.sh`'s final sweep. `t10`, `t11`, `t15`, `t21`, `t32` and `t34`
all assert this from different angles.

## 3. Privileges

| Component | Runs as | Needs root for |
|---|---|---|
| `install.sh` | root (required) | writing the unit, the program, the state dir |
| supervisor | root (systemd service or daemon) | `warp-cli`, `iptables`, reading opencode's log |
| `ouc hop` as a user | you | nothing — it drops a request file |
| `ouc status`, `ouc doctor` | anybody | nothing (read-only state) |

**No sudoers file is installed.** The original version shipped
`/etc/sudoers.d/opencode-unlimited` granting passwordless root for a script
the logged-in user could overwrite — that is local privilege escalation with
extra steps. This version has no such entry and deletes the old one on
install and on uninstall.

`/usr/local/bin/ouc` is a two-line `exec` wrapper (root-owned, `0755`);
the program itself lives in `/usr/local/lib/opencode-unlimited/ouc.py`
(root-owned, `0755`, not writable by your user).

## 4. The unprivileged hop channel

`ouc hop` as a normal user writes a small JSON request
(`{"id": …, "ts": …, "user": …}`) and waits for a result file. Two
locations, tried in order:

1. `~/.local/state/opencode-unlimited/hop.request` — owned by your user
2. `/tmp/opencode-unlimited/hop.request` — mode `1777`, only if (1) is not
   writable

The root supervisor polls both. Consequences you should know:

- On a **single-user** machine this is harmless.
- On a **shared** machine, any local user can ask the supervisor to rotate
  the tunnel — i.e. use up WARP registrations and briefly change the
  machine's egress. They cannot read your files, run arbitrary commands, or
  change configuration: the request contains no command, only an id.
- If you do not want that, delete `/tmp/opencode-unlimited` usage by making
  `~/.local/state` writable for the target user (the normal case), or do not
  install the tool on multi-user hosts.

## 5. What this tool never does

Explicitly, verified by tests:

- never writes, appends to, or filters opencode's output (`t02`);
- never creates or edits `AGENTS.md`, `CLAUDE.md`, prompt files, shell
  profiles, `bashrc`, cron of the user, or any config belonging to another
  program (`t01`, `t21`);
- never includes advertising, referral links, signatures, or instructions
  aimed at a language model (`t01`);
- never removes `~/.local/bin/opencode` unless it installed it there
  (`--wrap`), and restores the previous entry point otherwise (`t21`);
- never installs a sudoers rule, a self-restoring watchdog, or a second copy
  of itself;
- never deletes your WARP registration on uninstall (you may use WARP for
  other things);
- never sends telemetry. The only outbound connections are `warp-cli` to
  Cloudflare and the egress probe (`https://api64.ipify.org` by default,
  changeable with `OUC_PROBE_URL` or replaceable by your own command with
  `OUC_PROBE_CMD`).

## 6. Data written

| File | Content | Mode |
|---|---|---|
| `state.json` | offsets, hashes, hop counter, blacklisted `/64`s, last error | `0644` |
| `supervisor.log`, `ouc.log` | operational log, rotated, no payloads or prompts | `0644` |
| `supervisor.pid`, `heartbeat` | pid + timestamp | `0644` |
| `hop.request` / `hop.result` | request id, old/new block, timing | `0644` |

No prompts, no completions, no file contents, no tokens, no account
credentials are ever logged. The only thing read from opencode is the tail
of its own log, and only to look for the two quota strings.

## 7. Threat model, briefly

| Threat | Covered? |
|---|---|
| WARP drops, traffic leaks out of the real interface | Yes, by the leak guard (except during repairs — see §1) |
| Rotation fails and the machine loses internet | Yes — that is the bug this rewrite exists to fix |
| A stale firewall rule survives a crash | Yes — `ExecStopPost`, `SIGTERM` handler, uninstall sweep |
| A local user escalates to root through this tool | Yes — no sudoers, no user-writable root script |
| Cloudflare links your rotations to one device | No. New registrations can still be correlated by IP, timing or account. This is not an anonymity tool, and rotating does not hide you from Cloudflare. |
| Your ISP or a local observer sees the repair window | No — see §1. |
| Someone reads your `~/.local/share/opencode` logs | Out of scope; those are opencode's own files. |

## 8. Reporting / auditing

Everything is Python standard library in one file, plus three shell
scripts. Read it in an afternoon:

```bash
wc -l ouc.py install.sh uninstall.sh opencode-warp
grep -n "subprocess" ouc.py     # every external command it runs
grep -n "iptables" ouc.py       # every firewall change
grep -rn "AGENTS\|Telegram\|signature" ouc.py install.sh   # should be empty
```

To verify the claims yourself instead of trusting this document:

```bash
bash tests/run_tests.sh --offline
```

`t01` scans the shipped tree for advertising, `t02` asserts byte-identical
wrapper output, `t10`/`t32` assert you keep your internet when things break,
`t12` asserts the kill-switch can be disabled, `t21` asserts nothing is left
behind.
