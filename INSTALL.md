# INSTALL.md — opencode-unlimited **2.3.1**

A step-by-step install guide that assumes nothing. Every command in the
Linux and Codespaces sections was **run for real** during the test session
recorded in `TEST_RESULTS.md` (Debian 13 + GitHub Codespaces, opencode 1.19.5).
The macOS and Windows sections are the same tool driven through a Linux
environment; they are marked *not tested* where that is the honest answer.

---

## 0. What you received

```
opencode-unlimited-2.2.0.zip
├── ouc.py               the whole tool (one Python file, ~2900 lines)
├── install.sh           installer (root)
├── uninstall.sh         remover
├── opencode-warp        tiny wrapper: runs opencode through the pinned tunnel
├── README.md            what it does + why each design decision was made
├── INSTALL.md           ← you are here
├── UNINSTALL.md         how to remove it cleanly
├── SECURITY.md          what it touches, what it never touches
├── CHANGELOG.md         2.0.0 → 2.2.0 history
├── TEST_RESULTS.md      every test, every log, including the real wall
└── tests/               the test suite (safe offline mode included)
```

Nothing in this archive contains a credential, token, key, email address or
machine name. Unzip it anywhere; it installs nothing until you run `install.sh`.

---

## 1. Words used in this guide

| Term | Plain meaning |
|---|---|
| **WARP** | Cloudflare's free VPN client. It gives your machine a *new public IP address*. |
| **`warp-cli`** | The command you type to control WARP. |
| **Egress** | The IP address the outside world sees when you connect. |
| **IPv6 `/64`** | An address looks like `2a09:bac1:76a0:dba8::1c:331`. The first four groups (`2a09:bac1:76a0:dba8`) are the **block** — think of it as the *street* your address sits on. One `/64` street holds billions of addresses. The free tier counts your quota **per street**, so a fresh quota needs a fresh street. |
| **Rotation / hop** | Asking WARP for a new key, which usually lands your machine on a different street. |
| **Supervisor** | A small background program that watches opencode's log and rotates when a quota wall appears. |
| **systemd** | The program most Linux systems use to start background services at boot. |
| **Portable mode** | The same supervisor without systemd: one daemon, one PID file. Used in containers and on machines where you cannot enable units. |
| **Tunnel mode** | WARP takes over routing (a real VPN); the tool manages a firewall kill-switch. |
| **Proxy mode** | WARP exposes a local SOCKS5 proxy instead of changing routes. No TUN device, no firewall rules. Required in Codespaces/Docker/CI. |
| **TUN device** (`/dev/net/tun`) | The kernel device a normal VPN needs. Containers often do not have one. |
| **SOCKS5 / HTTP proxy** | Two ways to say "send my traffic through this local port". |
| **Kill-switch** | Firewall rules that stop traffic leaking out of your real connection while WARP is up. |
| **`ouc`** | The command this tool installs (`/usr/local/bin/ouc`). |

---

## 2. Which install path do I need?

| Your machine | Use this section | Why |
|---|---|---|
| Ubuntu / Debian / Mint / Pop!_OS / Raspberry Pi OS, with systemd | **§5 A** | normal case |
| Fedora / RHEL / Rocky / Alma | **§5 A**, but install WARP yourself (§4B) | the installer only auto-installs WARP via apt/dnf |
| Docker container, GitHub Codespace, devcontainer, CI runner | **§5 C** (proxy mode) | no TUN device, no firewall rights |
| Windows 10/11 | **§5 D** — inside **WSL2** | Cloudflare's CLI is Linux-only; WSL2 is a real Linux and works |
| macOS | **§5 E** — inside Docker or a small Linux VM | same reason: no Linux `warp-cli` on the Mac itself |
| Any Linux without systemd (Alpine, minimal image, no-root VPS) | **§5 B** (portable) | no units to enable |

Two requirements everywhere: **Python 3.8+** and **root/sudo** (for the
supervisor and, in tunnel mode, for the firewall). Proxy mode needs no
firewall at all.

---

## 3. Check the machine (10 seconds)

```bash
python3 -V                                   # 3.8 or newer
command -v warp-cli                          # WARP present?
command -v opencode                          # opencode CLI present?
ip -6 addr show scope global | head -3       # does this host even have IPv6?
ls -l /dev/net/tun                           # optional: tunnel mode / kill-switch
command -v iptables ip6tables                # optional: kill-switch
```

Missing `/dev/net/tun` or iptables is **not** fatal: the installer says so and
installs without a kill-switch. Missing IPv6 on the *host* is the one thing
rotation cannot fix — WARP itself does not need it, but a host that cannot
reach the internet over IPv6 at all cannot rotate IPv6 blocks (proxy mode will
tell you plainly: `egress family: IPv4 — the proxy is bypassed`).

---

## 4. Get WARP working once, by hand

The tool **rotates an existing tunnel**; it never replaces Cloudflare's client.
Do this once before installing.

### 4A. Linux (apt)

```bash
# repo + key (Debian/Ubuntu)
curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg \
  | sudo gpg --yes --dearmor -o /usr/share/keyrings/cloudflare-warp.gpg
echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp.gpg] https://pkg.cloudflareclient.com/ $(. /etc/os-release; echo $VERSION_CODENAME) main" \
  | sudo tee /etc/apt/sources.list.d/cloudflare-client.list
sudo apt-get update && sudo apt-get install -y cloudflare-warp
```

### 4B. Fedora / RHEL family (dnf)

```bash
sudo dnf install -y cloudflare-warp     # if your repos ship it
# otherwise: download the .rpm from https://pkg.cloudflareclient.com/
```

### 4C. Start it and register (all Linux)

```bash
sudo systemctl enable --now warp-svc        # or: sudo nohup /usr/bin/warp-svc &
warp-cli --accept-tos registration new
warp-cli --accept-tos connect
warp-cli --accept-tos status                # → Status update: Connected
```

If `registration new` complains that a registration already exists, run
`warp-cli --accept-tos registration delete`, wait ten seconds, then register
again. **Do not loop this** — Cloudflare throttles rapid re-registration
(measured: 3 of 10 rapid cycles were refused).

Confirm traffic is really leaving through WARP:

```bash
curl -s https://www.cloudflare.com/cdn-cgi/trace | grep -E '^(ip|colo|warp)='
# warp=on  and an ip= starting with 2a09:bac…  means you are inside the tunnel
```

---

## 5. Install the tool

```bash
unzip opencode-unlimited-2.2.0.zip
cd opencode-unlimited-fixed      # (or whatever the unzipped folder is called)
sudo bash install.sh
```

Options (all optional; the installer is idempotent — running it twice is safe):

| Option | Effect |
|---|---|
| *(none)* | systemd service if systemd is usable, otherwise portable mode |
| `--portable` | force the portable supervisor (no systemd at all) |
| `--proxy` | **proxy mode**: no TUN, no firewall, no routes (Codespaces/Docker/CI) |
| `--with-warp` | install `cloudflare-warp` via apt/dnf if `warp-cli` is missing |
| `--with-opencode` | install the opencode CLI if it is missing |
| `--wrap` | also point `~/.local/bin/opencode` at the pinned launcher (keeps a backup) |
| `-y`, `--yes` | non-interactive |

### 5A. Normal Linux, tunnel mode (recommended on a real box)

```bash
sudo bash install.sh
```

Healthy output looks like this (real run, Debian 13):

```
  ✓ target user: user  (home: /home/user)
  ✓ warp-cli: /usr/bin/warp-cli
  ✓ warp-svc: active
  ✓ kill-switch available (TUN device + iptables present)
  ✓ installed /usr/local/bin/ouc
  mode: systemd
  ✓ systemd unit installed and started
  ✓ warp tunnel: connected
  opencode-unlimited 2.2.0  (mode: systemd)
  ✓ supervisor: running (pid 32380)
  ✓ egress: 2a09:bac5:b045:3705::57b:25   block: 2a09:bac5:b045:3705
  ✓ kill-switch: installed
  ✓ rotations: 0   blacklisted blocks: 0
```

Service control:

```bash
systemctl status opencode-unlimited.service
sudo journalctl -u opencode-unlimited.service -f
```

### 5B. Linux without systemd (portable mode)

```bash
sudo bash install.sh --portable
sudo ouc start          # daemonise (writes a PID file)
ouc status              # alive? pid? heartbeat age?
sudo ouc stop           # stops it and removes the kill-switch
```

### 5C. Codespaces / Docker / devcontainer / CI — proxy mode

This is the mode the project was built *around* for containers: no TUN device,
no firewall, no route changes.

```bash
sudo bash install.sh --proxy --portable
```

What it does, in order:

1. installs Cloudflare WARP if `warp-cli` is absent;
2. `warp-cli registration new` **only if** no registration exists (an existing
   one is reused — deleting it just to reconnect is what used to break setups);
3. `warp-cli mode proxy` + `warp-cli proxy port 40000` — no TUN, no iptables;
4. `warp-cli connect`, then waits up to ~30 s for `Connected`;
5. starts the supervisor and the embedded bridge `http://127.0.0.1:8080 → socks5://127.0.0.1:40000`;
6. leaves the kill-switch and `/etc/hosts` pin **off**, and records why.

Then point opencode at it — either by environment:

```bash
export HTTPS_PROXY=http://127.0.0.1:8080 HTTP_PROXY=http://127.0.0.1:8080
opencode run "hello"
```

…or let the tool do it (recommended; this is the only way to be sure opencode
does not fall back to a direct connection):

```bash
ouc lane run "hello"
```

> `HTTPS_PROXY` is honoured by opencode — verified by pointing it at a dead
> port and watching it fail with `ECONNREFUSED`. Do **not** rely on
> `LD_PRELOAD` tricks (torsocks etc.) to redirect opencode; they were measured
> to be unreliable with a Node-based client.

### 5D. Windows 10/11 → WSL2

Cloudflare ships `warp-cli` for Linux only, so run the tool in WSL2:

```powershell
wsl --install -d Ubuntu          # once, then reboot if asked
```

Inside the Ubuntu shell:

```bash
# WARP's Linux client does not work with WSL's default networking for TUN;
# use proxy mode, which needs none of it.
unzip opencode-unlimited-2.2.0.zip && cd opencode-unlimited-fixed
sudo bash install.sh --proxy --portable
```

Notes for WSL specifically:

* WSL2 may not provide inbound/outbound IPv6 depending on your Windows build —
  if `ip -6 addr show scope global` is empty, `ouc doctor` will say
  `egress family: IPv4` and rotation cannot change your quota;
* opencode must be installed **inside WSL** (`curl -fsSL https://opencode.ai/install | bash`)
  and run from there, so that it inherits `HTTPS_PROXY`;
* you can start the whole thing from Windows with
  `wsl -d Ubuntu -u root /usr/local/bin/ouc start`.
* *Status: same code path as §5C, which is tested; WSL itself was not part of
  the test session.*

### 5E. macOS → Docker (or a Linux VM)

There is no Linux `warp-cli` on macOS, so use a Linux container:

```bash
docker run -it --rm --network host -v "$PWD":/w ubuntu:24.04 bash
# inside the container:
apt-get update && apt-get install -y python3 curl unzip
cd /w && unzip opencode-unlimited-2.2.0.zip && cd opencode-unlimited-fixed
bash install.sh --proxy --portable
```

With `--network host` the container's ports 40000 (SOCKS5) and 8080 (bridge)
appear on your Mac, so any client — including opencode run on macOS itself —
can use `HTTPS_PROXY=http://127.0.0.1:8080`.
*Status: proxy mode itself is tested in a container; this exact macOS/Docker
recipe was not part of the test session.*

---

## 6. Check it in five seconds

```bash
ouc status
```

You want, in this order:

| Line | Meaning |
|---|---|
| `supervisor: running (pid …)` | the watcher is alive |
| `warp tunnel: Connected` | the VPN is up |
| `egress: 2a09:…  block: 2a09:…` | you have an IPv6 street, and this is its name |
| `kill-switch: installed` (tunnel mode only) | leaks are blocked |
| `warp mode: proxy (no TUN)` + `http bridge: http://127.0.0.1:8080` (proxy mode) | container path is wired |

In tunnel mode also check the pin:

```
✓ ipv6 pin: on (2606:4700:10::6814:2011, 2606:4700:10::ac42:ad95) — opencode uses the tunnel
```

If it says `off`, opencode may reach Cloudflare over IPv4 — where rotating a
`/64` changes nothing. Turn it on with `sudo ouc pin on` (one managed block in
`/etc/hosts`, removed again by `ouc pin off`).

---

## 7. Daily use

```bash
ouc run "your task" # run opencode; on a free limit it waits for a working
                    # block and continues the SAME session (2.3.0)
ouc resume          # do that continuation on demand
ouc status          # what am I connected to right now?
ouc hop             # rotate to a fresh block now (asks the root supervisor)
ouc verify          # one real request: exit 0 = quota works, 1 = still limited
ouc doctor          # full health check, with a proxy block in proxy mode
ouc lane run "hi"   # run opencode with the tunnel guaranteed (proxy mode)
```

Nothing needs to run by hand in normal operation: the supervisor detects the
wall (`Free usage limit reached`) in opencode's log, rotates, and verifies
with a real request. Measured end-to-end: **wall → working block in 7 s**.

Useful extras: `ouc repair` (tunnel lost, registration gone),
`ouc rescue` (panic button: stops the tool, removes every firewall rule it
added, reconnects WARP), `ouc config` (effective settings),
`ouc scan-once --no-rotate` (one detection pass, no action).

---

## 8. When something looks wrong

| Symptom | Meaning | Do this |
|---|---|---|
| `supervisor: not running` | nothing is watching the log | `sudo ouc start`, or re-run `sudo bash install.sh` |
| `warp tunnel: Status update: Unable` | WARP lost its registration | `sudo warp-cli --accept-tos registration new && sudo warp-cli --accept-tos connect`, or `sudo ouc repair` |
| `egress: 136.x.x.x` (IPv4) | traffic bypasses the tunnel | `sudo ouc repair`, then `ouc status` |
| `kill-switch: NOT installed  DEGRADED` | installed without firewall on purpose | nothing dangerous, but your real IP is visible: `sudo ouc repair` |
| `ouc hop` → *supervisor is not running* | the service really is down | `sudo systemctl start opencode-unlimited` or `sudo ouc start` |
| `opencode` still says *Free usage limit reached* right after a hop | that block was already spent by other people | `ouc hop` again, then `ouc verify`; expect roughly 1 in 3 rotations to land on a dead block |
| `port 8080 already in use` (proxy mode) | another local service owns the bridge port | `OUC_PROXY_HTTP_PORT=8081 sudo bash install.sh --proxy`, then use that port |
| No internet on the whole box | should not happen | `sudo ouc rescue` |

---

## 9. Self-test before you trust it

```bash
bash tests/run_tests.sh --offline   # safe anywhere, touches no network
sudo bash tests/run_tests.sh        # adds the live tests (needs working WARP)
```

The offline suite is safe on a production machine: it never changes routes or
firewall state. The live tests restore the tunnel afterwards, including the one
that deliberately breaks it.

---

## 10. Uninstall

```bash
sudo bash uninstall.sh
```

Removes the unit/daemon, `/usr/local/bin/ouc`, the state directory, the
kill-switch rules and the `/etc/hosts` pin. **Leaves opencode itself, your
sessions, and the `cloudflare-warp` package untouched.** Details:
`UNINSTALL.md`.

---

## 11. Security notes (short version)

* No tokens, keys or account data are stored in this archive, and the tool
  never asks for any.
* It never touches `~/.ssh`, your opencode configuration, your API keys, your
  git history, or other projects.
* In proxy mode it changes **nothing** outside its own state directory and two
  loopback ports.
* Full details and the threat model: `SECURITY.md`.

---

## 12. Handing this to someone else

Send the zip. All they need is:

1. a Linux machine or container (WSL2/Docker on Windows/macOS — §5D/§5E),
2. `python3` + `curl` + `unzip`,
3. root — or, in proxy mode, a container where they are root anyway,
4. no account, no GitHub login, no API key: WARP registers itself anonymously.

Then: `unzip` → `sudo bash install.sh --proxy --portable` (containers) or
`sudo bash install.sh` (a real Linux box) → `ouc status`.

If they only want to read what it does before running it: `README.md` first,
then `SECURITY.md`, then this file. The test evidence — including a genuine
free-tier wall detected and recovered in 7 s — is in `TEST_RESULTS.md`.
