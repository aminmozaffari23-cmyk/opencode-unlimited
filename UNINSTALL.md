# UNINSTALL.md — opencode-unlimited 2.0.1

Removal is designed to be complete **and** to leave your network working.
The automated test `t21_uninstall_complete` checks every path below, and
`t34_real_uninstall` checks that the machine is still online over IPv6
afterwards (`egress=2a09:bac1:76e0:dba8::2e9:1f`, zero firewall rules left).

---

## 1. Normal removal

```bash
cd opencode-unlimited-fixed
sudo bash uninstall.sh
```

Expected output:

```
  removing opencode-unlimited ...
  stopping supervisor...
  removing systemd units...
    removed /etc/systemd/system/opencode-unlimited.service
  removing firewall...
  removing legacy sudoers entry...
  removing legacy advertising file...
  restoring the opencode entry point...
  removing legacy + current files...
    removed /usr/local/lib/opencode-unlimited
    removed /usr/local/bin/ouc
    removed /var/lib/opencode-unlimited
  uninstalled. opencode itself and your sessions are untouched.
  (the cloudflare-warp package is left installed on purpose)
  checking that the network is not firewalled...
  ✓ no leak-guard rules left

  ✓ uninstalled. opencode itself, your sessions and cloudflare-warp are untouched.
```

Right afterwards, on the test machine: `curl -s -6 https://api64.ipify.org` →
`2a09:bac5:b045:3705::57b:25`, `iptables -S OUTPUT` → no `OUC` rules. Internet
intact, nothing left behind.

What that does, in order:

1. Stops the service (`systemctl disable --now`) or the portable supervisor
   (SIGTERM to the PID in `/var/lib/opencode-unlimited/supervisor.pid`,
   wait, then SIGKILL if it ignores the first signal).
2. Removes every firewall rule the tool created — `OUC_LEAKGUARD` in both
   `iptables` and `ip6tables` — *before* anything else, so a half-finished
   uninstall can never firewall you in.
3. Deletes its own files and state.
4. Re-checks the firewall and removes any rule that survived.
5. Prints exactly what it did not touch.

## 2. If `ouc` is already gone

`uninstall.sh` falls back to running the bundled `ouc.py` directly, and if
that is gone too it performs the whole cleanup with plain shell commands
(unit, firewall, files). Nothing needs to be installed for the uninstaller
to work.

## 3. What is removed

| Path | Note |
|---|---|
| `/etc/systemd/system/opencode-unlimited.service` | the root unit |
| `/usr/local/lib/opencode-unlimited/` | the program |
| `/usr/local/bin/ouc`, `/usr/local/bin/opencode-warp` | entry points |
| `/var/lib/opencode-unlimited/` | state, logs, pidfile, locks |
| `~/.local/state/opencode-unlimited/` | the hop request channel |
| `/tmp/opencode-unlimited/` | fallback hop channel |
| `~/.local/bin/opencode` | **only** if the installer replaced it; the backup `opencode.pre-ouc` is restored |

### Leftovers from the original v1 version (also removed)

| Path | Why it must go |
|---|---|
| `/etc/sudoers.d/opencode-unlimited` | blanket `NOPASSWD` rule for a user-writable script |
| `~/.config/systemd/user/opencode-unlimited-guard.service` | user-session unit |
| `/usr/local/bin/opencode-unlimited-keeper` | self-restoring watchdog |
| `~/.local/bin/opencode-warp` | v1 launcher |
| `~/.local/share/opencode-unlimited/` | v1 state |
| `~/.opencode/AGENTS.md` | the planted advertising file |
| `/etc/systemd/system/opencode-unlimited-keeper.service` | v1 keeper unit |

The ad file is recognised by a marker that is stored base64-encoded and
decoded only at runtime, so the repository itself contains no advertising
text. Removal is by deletion only — there is no self-restoring mechanism
anywhere in this version, so the file stays gone.

## 4. What is deliberately *not* removed

- **Cloudflare WARP and your WARP registration.** You may use it for other
  things. To remove that too:

  ```bash
  sudo warp-cli --accept-tos disconnect
  sudo warp-cli --accept-tos registration delete
  sudo apt-get remove cloudflare-warp      # optional
  ```

  If you later reinstall this tool, the installer registers a new device
  automatically.

- **opencode itself**, your sessions, your logs, your shell configuration.
  Nothing in your shell profile is ever edited by this tool.

## 5. Verify the removal

```bash
command -v ouc || echo "ouc gone"
systemctl status opencode-unlimited.service    # should be "could not be found"
sudo iptables -S OUTPUT | grep OUC || echo "no v4 rules"
sudo ip6tables -S OUTPUT | grep OUC || echo "no v6 rules"
ls /var/lib/opencode-unlimited 2>/dev/null || echo "state gone"
curl -s -6 https://api64.ipify.org || echo "IPv6 egress gone (WARP removed)"
```

## 6. Emergency: something is blocking my traffic

If you suspect a firewall rule survived, or the machine has no internet:

```bash
sudo ouc rescue          # if ouc still exists
```

or, if it does not:

```bash
sudo iptables  -D OUTPUT -j OUC_LEAKGUARD 2>/dev/null
sudo iptables  -F OUC_LEAKGUARD 2>/dev/null
sudo iptables  -X OUC_LEAKGUARD 2>/dev/null
sudo ip6tables -D OUTPUT -j OUC_LEAKGUARD 2>/dev/null
sudo ip6tables -F OUC_LEAKGUARD 2>/dev/null
sudo ip6tables -X OUC_LEAKGUARD 2>/dev/null
```

Those six commands remove the entire leak guard. They are the same ones
`ouc rescue` runs, and they are safe to run at any time.
