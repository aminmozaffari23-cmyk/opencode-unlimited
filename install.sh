#!/bin/bash
# opencode-unlimited (corrected) — installer
#
# Design goals:
#   * never depends on a systemd *user* session (works in Docker/CI/headless)
#   * never installs a sudoers entry (privileged work lives in a root service)
#   * never plants agent rules / advertising of any kind
#   * safe to run twice (idempotent), safe to run over an old version (cleans it)
set -u

BRAND="opencode-unlimited"
VERSION="2.3.0"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]:-}")" && pwd)"

PREFIX="${OUC_PREFIX:-/usr/local}"
LIB_DIR="${OUC_LIB_DIR:-$PREFIX/lib/$BRAND}"
BIN_PATH="${OUC_BIN_PATH:-$PREFIX/bin/ouc}"
UNIT_DIR="${OUC_UNIT_DIR:-/etc/systemd/system}"
STATE_DIR="${OUC_STATE_DIR:-/var/lib/$BRAND}"

WANT_WARP=0
WANT_OPENCODE=0
FORCE_PORTABLE=0
FORCE_SYSTEMD=0
WRAP=0
ASSUME_YES=0

say(){ printf '  %s\n' "$*"; }
ok(){  printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn(){ printf '  \033[33m!\033[0m %s\n' "$*"; }
bad(){ printf '  \033[31m✗\033[0m %s\n' "$*"; }
die(){ bad "$*"; exit 1; }

for arg in "$@"; do
  case "$arg" in
    --with-warp)       WANT_WARP=1 ;;
    --with-opencode)   WANT_OPENCODE=1 ;;
    --portable)        FORCE_PORTABLE=1 ;;
    --systemd)         FORCE_SYSTEMD=1 ;;
    --proxy)           FORCE_PROXY=1 ;;
    --wrap)            WRAP=1 ;;
    -y|--yes)          ASSUME_YES=1 ;;
    -h|--help)
      cat <<EOF
usage: install.sh [options]

  --with-warp        install cloudflare-warp if warp-cli is missing
  --with-opencode    install the opencode CLI if it is missing
  --portable         force no-systemd mode (daemon + pidfile)
  --systemd          force systemd mode (writes the unit even if this machine has
                     no running systemd, e.g. while building a container image)
  --proxy            proxy mode: WARP without a TUN device (Codespaces, Docker, CI).
                     No firewall, no /etc/hosts pin; opencode reaches the tunnel
                     through a local HTTP proxy instead.
  --wrap             also point ~/.local/bin/opencode at the IPv6-pinned launcher
                     (a backup of the previous entry point is kept)
  -y, --yes          non-interactive
EOF
      exit 0 ;;
    *) die "unknown option: $arg" ;;
  esac
done

echo ""
echo "  ======================================="
echo "  $BRAND $VERSION — installer"
echo "  ======================================="
echo ""

# ---------------------------------------------------------------- preflight
[ "$(id -u)" = 0 ] || die "run me with root (sudo bash install.sh); no sudoers entry is created"
command -v python3 >/dev/null 2>&1 || die "python3 is required"
command -v curl    >/dev/null 2>&1 || warn "curl missing — the egress probe will use python instead"

TARGET_USER="${SUDO_USER:-${OUC_TARGET_USER:-}}"
if [ -z "$TARGET_USER" ] || [ "$TARGET_USER" = "root" ]; then
  TARGET_USER="${OUC_TARGET_USER:-$(logname 2>/dev/null || echo root)}"
fi
if [ "$TARGET_USER" = "root" ]; then
  TARGET_HOME="${OUC_TARGET_HOME:-/root}"
else
  TARGET_HOME="${OUC_TARGET_HOME:-$(getent passwd "$TARGET_USER" | cut -d: -f6)}"
fi
[ -n "$TARGET_HOME" ] || die "could not determine the target user's home; set OUC_TARGET_HOME"
ok "target user: $TARGET_USER  (home: $TARGET_HOME)"

# ---------------------------------------------------------------- legacy cleanup
# An old (v1) install hijacks ~/.local/bin/opencode, plants an advertising
# AGENTS.md, writes a broad sudoers entry and installs a user-level unit.
legacy_found=0
[ -f "/etc/sudoers.d/$BRAND" ] && legacy_found=1
[ -f "/etc/systemd/system/$BRAND-keeper.service" ] && legacy_found=1
[ -f "$TARGET_HOME/.config/systemd/user/$BRAND-guard.service" ] && legacy_found=1
[ -f "$TARGET_HOME/.local/share/$BRAND/scripts/zen-guard.sh" ] && legacy_found=1
if [ "$legacy_found" = 1 ]; then
  say "old version detected — cleaning it up first"
  if [ -x "$BIN_PATH" ]; then
    OUC_TARGET_HOME="$TARGET_HOME" "$BIN_PATH" uninstall >/dev/null 2>&1 || true
  fi
  systemctl disable --now "$BRAND-keeper.service" >/dev/null 2>&1 || true
  systemctl --user disable --now "$BRAND-guard.service" >/dev/null 2>&1 || true
  rm -f "/etc/systemd/system/$BRAND-keeper.service" "/usr/local/bin/$BRAND-keeper" 2>/dev/null || true
  rm -f "$TARGET_HOME/.config/systemd/user/$BRAND-guard.service" 2>/dev/null || true
  rm -f "/etc/sudoers.d/$BRAND" 2>/dev/null || true
  rm -rf "$TARGET_HOME/.local/share/$BRAND" 2>/dev/null || true
  rm -f "$TARGET_HOME/.local/bin/$BRAND-hop" "$TARGET_HOME/.local/bin/$BRAND-doctor" 2>/dev/null || true
  systemctl daemon-reload >/dev/null 2>&1 || true
  # only remove the planted file if it actually carries the old ad marker
  ad="$TARGET_HOME/.opencode/AGENTS.md"
  # the old ad marker is decoded at runtime so this repo carries no ad text
  marker="$(python3 -c 'import base64;print(base64.b64decode("RWxlY3Ryb25DdXJzZWQ=").decode())' 2>/dev/null)"
  if [ -f "$ad" ] && [ -n "$marker" ] && grep -qF "$marker" "$ad" 2>/dev/null; then
    rm -f "$ad"; ok "removed the old advertising file"
  fi
  ok "old version cleaned"
fi

# ---------------------------------------------------------------- dependencies
if ! command -v warp-cli >/dev/null 2>&1; then
  if [ "$WANT_WARP" = 1 ]; then
    say "installing cloudflare-warp"
    codename="$(. /etc/os-release 2>/dev/null; echo "${VERSION_CODENAME:-}")"
    if command -v apt-get >/dev/null 2>&1 && [ -n "$codename" ]; then
      curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg \
        | gpg --yes --dearmor -o /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg 2>/dev/null
      echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ $codename main" \
        > /etc/apt/sources.list.d/cloudflare-client.list
      apt-get update -qq >/dev/null 2>&1
      DEBIAN_FRONTEND=noninteractive apt-get install -y -qq cloudflare-warp >/dev/null 2>&1 || true
    elif command -v dnf >/dev/null 2>&1; then
      dnf -y install cloudflare-warp >/dev/null 2>&1 || true
    elif command -v pacman >/dev/null 2>&1; then
      die "on Arch install cloudflare-warp-bin from the AUR, then re-run"
    fi
  else
    warn "warp-cli not found — re-run with --with-warp, or install it yourself"
  fi
fi
command -v warp-cli >/dev/null 2>&1 && ok "warp-cli: $(command -v warp-cli)" \
  || warn "warp-cli missing: rotation will not work until it is installed"

# the WARP daemon must be running; on some packages it is not enabled by default
if command -v warp-cli >/dev/null 2>&1 && command -v systemctl >/dev/null 2>&1 \
   && [ -d /run/systemd/system ]; then
  if ! systemctl is-active --quiet warp-svc 2>/dev/null; then
    say "starting warp-svc (it is not enabled by default on every distro)"
    systemctl enable --now warp-svc >/dev/null 2>&1 || warn "could not start warp-svc"
  fi
  systemctl is-active --quiet warp-svc 2>/dev/null && ok "warp-svc: active" \
    || warn "warp-svc not active — 'sudo systemctl start warp-svc'"
fi

OC_BIN="$TARGET_HOME/.opencode/bin/opencode"
if [ ! -x "$OC_BIN" ]; then
  if [ "$WANT_OPENCODE" = 1 ]; then
    say "installing the opencode CLI"
    curl -fsSL https://opencode.ai/install 2>/dev/null | bash >/dev/null 2>&1 || true
  fi
fi
[ -x "$OC_BIN" ] && ok "opencode: $OC_BIN" \
  || warn "opencode CLI not found at $OC_BIN (install it, or keep using your own)"

# ---------------------------------------------------------------- capabilities
TUN_OK=0; [ -e /dev/net/tun ] && TUN_OK=1
IPT_OK=0; { command -v iptables >/dev/null 2>&1 || [ -x /usr/sbin/iptables ]; } \
          && { command -v ip6tables >/dev/null 2>&1 || [ -x /usr/sbin/ip6tables ]; } && IPT_OK=1
KILL_SWITCH=1
# ------------------------------------------------------------- proxy mode
# Codespaces / Docker / CI / unprivileged pods have no TUN device. WARP's proxy
# mode needs none: it publishes a local SOCKS5 proxy and still reaches the IPv6
# internet, so the /64 rotation keeps working.
WARP_MODE="warp"
if [ "${FORCE_PROXY:-0}" = 1 ] || [ "${CODESPACES:-}" = "true" ] || [ "$TUN_OK" = 0 ]; then
  WARP_MODE="proxy"
fi
if [ "$WARP_MODE" = "proxy" ]; then
  ok "proxy mode (no TUN needed): WARP SOCKS5 + local HTTP bridge"
  KILL_SWITCH=0
fi
if [ "$TUN_OK" = 0 ] && [ "$WARP_MODE" != "proxy" ]; then
  warn "/dev/net/tun missing — TUN mode impossible here"
  KILL_SWITCH=0
fi
if [ "$IPT_OK" = 0 ]; then
  warn "iptables/ip6tables missing — the kill-switch will be disabled"
  KILL_SWITCH=0
fi
[ "$KILL_SWITCH" = 1 ] && ok "kill-switch available (TUN device + iptables present)" \
  || warn "continuing WITHOUT the kill-switch — manual hop still works"

# ---------------------------------------------------- stop any previous install
# An old supervisor running the old code (or an old systemd unit) would fight
# the new one: two watchers, two rotations, confusing logs.
if command -v systemctl >/dev/null 2>&1; then
  systemctl stop opencode-unlimited.service >/dev/null 2>&1 || true
fi
# the portable daemon runs in-process, so its command line is "start --portable",
# not "supervise": match the whole installed path instead of one subcommand
"${BIN_PATH:-/usr/local/bin/ouc}" stop >/dev/null 2>&1 || true
# Kill ONLY supervisors that run *this* installation's ouc.py. A blanket
# `pkill -f "opencode-unlimited/ouc.py"` also matches a second installation
# (a test sandbox, a staging copy, another user's prefix) and would silently
# take a production daemon down.
for _pid in $(pgrep -f "ouc\.py (start|supervise|bridge)" 2>/dev/null); do
  [ "$_pid" = "$$" ] && continue
  _cmd=$(tr '\0' ' ' < "/proc/$_pid/cmdline" 2>/dev/null) || continue
  case "$_cmd" in
    *"$LIB_DIR/ouc.py"*) ;;
    *) continue ;;
  esac
  _st=$(tr '\0' '\n' < "/proc/$_pid/environ" 2>/dev/null | sed -n 's/^OUC_STATE_DIR=//p' | head -1)
  if [ -n "$_st" ] && [ "$_st" != "$STATE_DIR" ]; then continue; fi
  kill "$_pid" 2>/dev/null || true
done
for _ in $(seq 1 20); do
  _left=0
  for _pid in $(pgrep -f "ouc\.py (start|supervise|bridge)" 2>/dev/null); do
    _cmd=$(tr '\0' ' ' < "/proc/$_pid/cmdline" 2>/dev/null) || continue
    case "$_cmd" in *"$LIB_DIR/ouc.py"*) _left=1 ;; esac
  done
  [ "$_left" = 0 ] && break
  sleep 0.5
done

# ---------------------------------------------------------------- install files
mkdir -p "$LIB_DIR" "$(dirname "$BIN_PATH")" "$STATE_DIR"
install -m 0755 "$SRC/ouc.py" "$LIB_DIR/ouc.py" || die "could not copy ouc.py"
cat > "$BIN_PATH" <<EOF
#!/bin/sh
exec python3 "$LIB_DIR/ouc.py" "\$@"
EOF
chmod 0755 "$BIN_PATH"
install -m 0755 "$SRC/opencode-warp" "$(dirname "$BIN_PATH")/opencode-warp" 2>/dev/null || true
ok "installed $BIN_PATH"

# what the supervisor needs to know: whose log to watch
WARP_MODE_INSTALL="$WARP_MODE" python3 - "$STATE_DIR/config.json" "$TARGET_HOME" "$KILL_SWITCH" <<'PY'
import json, sys, os
path, home, ks = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
cfg = {}
if os.path.exists(path):
    try: cfg = json.load(open(path))
    except Exception: cfg = {}
proxy = os.environ.get("WARP_MODE_INSTALL", "warp") == "proxy"
cfg.update({"target_home": home, "kill_switch": ks, "version": "$VERSION",
            "settle": max(8, int(cfg.get("settle", 9))), "cooldown": int(cfg.get("cooldown", 90)),
            "pin": False if proxy else bool(cfg.get("pin", True)),
            "verify": bool(cfg.get("verify", True)),
            "rotation_mode": "auto", "blacklist_ttl": int(cfg.get("blacklist_ttl", 14400)),
            "warp_mode": os.environ.get("WARP_MODE_INSTALL", "auto")})
json.dump(cfg, open(path, "w"), indent=2, sort_keys=True)
PY
chmod 0644 "$STATE_DIR/config.json" 2>/dev/null || true

# ---------------------------------------------------------------- mode choice
MODE="systemd"
if [ "$FORCE_PORTABLE" = 1 ]; then MODE="portable"; fi
if [ "$FORCE_SYSTEMD" != 1 ] && { ! command -v systemctl >/dev/null 2>&1 || [ ! -d /run/systemd/system ]; }; then
  MODE="portable"
fi
if [ "$FORCE_SYSTEMD" = 1 ]; then MODE="systemd"; fi
# A *user* session is irrelevant here: we never touch systemd --user.
say "mode: $MODE$([ "$MODE" = portable ] && echo ' (no systemd session needed)')"
python3 - "$STATE_DIR/config.json" "$MODE" <<'PY_MODE'
import json, sys, os
path, mode = sys.argv[1], sys.argv[2]
cfg = {}
if os.path.exists(path):
    try: cfg = json.load(open(path))
    except Exception: cfg = {}
cfg["mode"] = mode
json.dump(cfg, open(path, "w"), indent=2, sort_keys=True)
PY_MODE

if [ "$MODE" = "systemd" ]; then
  mkdir -p "$UNIT_DIR"
  cat > "$UNIT_DIR/opencode-unlimited.service" <<EOF
[Unit]
Description=opencode-unlimited supervisor (WARP keepalive, quota rotation, leak guard)
After=network-online.target warp-svc.service
Wants=network-online.target

[Service]
Type=simple
ExecStart=$BIN_PATH supervise --foreground
Environment=OUC_STATE_DIR=$STATE_DIR
Environment=OUC_TARGET_HOME=$TARGET_HOME
Environment=OUC_WARP_MODE=$WARP_MODE
Environment=OUC_PROXY_SOCKS_PORT=${OUC_PROXY_SOCKS_PORT:-40000}
Environment=OUC_PROXY_HTTP_PORT=${OUC_PROXY_HTTP_PORT:-8080}
Restart=always
RestartSec=10
ExecStopPost=-$BIN_PATH killswitch off

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload >/dev/null 2>&1
  if systemctl enable --now opencode-unlimited.service >/dev/null 2>&1; then
    ok "systemd unit installed and started"
  else
    warn "could not enable the unit — falling back to portable mode"
    MODE="portable"
  fi
fi

if [ "$MODE" = "portable" ]; then
  # a unit left over from a previous install would start a second supervisor
  if command -v systemctl >/dev/null 2>&1; then
    systemctl disable opencode-unlimited.service >/dev/null 2>&1 || true
    rm -f "$UNIT_DIR/opencode-unlimited.service" 2>/dev/null || true
    systemctl daemon-reload >/dev/null 2>&1 || true
  fi
  OUC_STATE_DIR="$STATE_DIR" OUC_TARGET_HOME="$TARGET_HOME" \
    "$BIN_PATH" start --portable || die "could not start the portable supervisor"
  # survive a reboot without systemd: optional crontab hook
  if [ "$ASSUME_YES" = 1 ] && command -v crontab >/dev/null 2>&1; then
    ( crontab -l 2>/dev/null | grep -v "ouc start" ; \
      echo "@reboot OUC_STATE_DIR=$STATE_DIR OUC_TARGET_HOME=$TARGET_HOME $BIN_PATH start --portable" \
    ) | crontab - 2>/dev/null || true
  fi
fi

# ---------------------------------------------------------------- optional wrap
if [ "$WRAP" = 1 ]; then
  entry="$TARGET_HOME/.local/bin/opencode"
  mkdir -p "$(dirname "$entry")"
  if [ -f "$entry" ] && ! grep -q "opencode-warp" "$entry" 2>/dev/null; then
    cp "$entry" "$entry.pre-ouc" 2>/dev/null || true
    say "backed up the previous entry point to $entry.pre-ouc"
  fi
  cat > "$entry" <<EOF
#!/usr/bin/env bash
# installed by $BRAND: pins opencode.ai to IPv6 and execs the real binary.
# This wrapper does not read, rewrite or annotate the program's output.
exec "$(dirname "$BIN_PATH")/opencode-warp" "\$@"
EOF
  chmod 0755 "$entry"
  ok "opencode → IPv6-pinned launcher (backup kept)"
fi

# ---------------------------------------------------------------- verify
echo ""
# ------------------------------------------------------- make the tunnel real
if command -v warp-cli >/dev/null 2>&1 && [ "$WARP_MODE" = "proxy" ]; then
  if ! pgrep -x warp-svc >/dev/null 2>&1; then
    say "starting warp-svc directly (no systemd in this environment)"
    nohup /usr/bin/warp-svc >/var/log/warp-svc-manual.log 2>&1 &
    sleep 4
  fi
fi
if command -v warp-cli >/dev/null 2>&1; then
  tunnel_up() { warp-cli --accept-tos status 2>/dev/null | head -1 | grep -q "Connected"; }
  if [ "$WARP_MODE" = "proxy" ]; then
    warp-cli --accept-tos mode proxy >/dev/null 2>&1 || true
    warp-cli --accept-tos proxy port "${OUC_PROXY_SOCKS_PORT:-40000}" >/dev/null 2>&1 || true
  fi
  warp-cli --accept-tos connect >/dev/null 2>&1 || true
  for _ in $(seq 1 10); do tunnel_up && break; sleep 2; done
  if ! tunnel_up; then
    # never delete a registration that exists: in proxy mode a reconnect can
    # take a few seconds, and deleting it costs a full re-registration
    if ! warp-cli --accept-tos registration show >/dev/null 2>&1; then
      warn "WARP has no registration — creating one"
      for attempt in 1 2 3; do
        if warp-cli --accept-tos registration new >/dev/null 2>&1; then break; fi
        sleep $((3 * attempt))
      done
    else
      say "WARP registration present — reconnecting (not re-registering)"
    fi
    if [ "$WARP_MODE" = "proxy" ]; then
      warp-cli --accept-tos mode proxy >/dev/null 2>&1 || true
      warp-cli --accept-tos proxy port "${OUC_PROXY_SOCKS_PORT:-40000}" >/dev/null 2>&1 || true
    else
      warp-cli --accept-tos mode warp >/dev/null 2>&1 || true
    fi
    warp-cli --accept-tos connect >/dev/null 2>&1 || true
    for _ in $(seq 1 15); do tunnel_up && break; sleep 2; done
  fi
  if tunnel_up; then
    ok "warp tunnel: connected"
  else
    warn "warp tunnel did NOT come up — run: sudo warp-cli --accept-tos registration new && sudo warp-cli --accept-tos connect"
  fi
fi

say "verifying..."
sleep 1
OUC_STATE_DIR="$STATE_DIR" OUC_TARGET_HOME="$TARGET_HOME" "$BIN_PATH" status || true
echo ""
ok "done. Commands:"
say "  sudo ouc status      # health"
say "  ouc hop              # rotate to a fresh /64 now (asks the supervisor)"
say "  sudo ouc rescue      # emergency: make the network work again"
echo ""
if [ "$WARP_MODE" = "proxy" ]; then
  say "Proxy mode: opencode must use the local bridge. Either run it through the"
  say "launcher (ouc lane <args>) or export it yourself:"
  say "  export HTTPS_PROXY=http://127.0.0.1:${OUC_PROXY_HTTP_PORT:-8080}"
else
  say "Set OUC_DISABLE_KILL_SWITCH=1 in the unit to run without the firewall."
  say "See SECURITY.md for what the repair window exposes."
fi
echo ""
