#!/bin/bash
# opencode-unlimited — uninstaller
#
# Removes: the unit/supervisor, the kill-switch chain, our files, our state,
# the loopback IPv6-pinned launcher, AND leftovers from the old v1 version
# (user-level unit, keeper unit, broad sudoers entry, planted advertising file,
# hijacked ~/.local/bin/opencode).
set -u
BRAND="opencode-unlimited"
PREFIX="${OUC_PREFIX:-/usr/local}"
BIN_PATH="${OUC_BIN_PATH:-$PREFIX/bin/ouc}"
# These three used to be hard-coded to /usr/local and /var/lib, which meant that
# running the test suite (or any sandboxed uninstall) on a machine that also had
# a real installation deleted the real one. They now follow the same overrides
# as everything else.
LIB_DIR="${OUC_LIB_DIR:-$PREFIX/lib/opencode-unlimited}"
STATE_DIR="${OUC_STATE_DIR:-/var/lib/opencode-unlimited}"
WARP_WRAPPER="${OUC_PREFIX:-/usr/local}/bin/opencode-warp"

say(){ printf '  %s\n' "$*"; }
ok(){  printf '  \033[32m✓\033[0m %s\n' "$*"; }
die(){ printf '  \033[31m✗\033[0m %s\n' "$*"; exit 1; }

[ "$(id -u)" = 0 ] || die "run me with root (sudo bash uninstall.sh)"

echo ""
say "removing $BRAND ..."
if [ -x "$BIN_PATH" ]; then
  "$BIN_PATH" uninstall --assume-root "$@" || true
else
  # ouc is already gone: do the cleanup by hand
  SRC="$(cd "$(dirname "${BASH_SOURCE[0]:-}")" && pwd)"
  if [ -f "$SRC/ouc.py" ]; then
    python3 "$SRC/ouc.py" uninstall --assume-root "$@" || true
  else
    systemctl disable --now opencode-unlimited.service 2>/dev/null || true
    rm -f /etc/systemd/system/opencode-unlimited.service
    systemctl daemon-reload 2>/dev/null || true
    iptables  -D OUTPUT -j OUC_LEAKGUARD 2>/dev/null; iptables  -F OUC_LEAKGUARD 2>/dev/null; iptables  -X OUC_LEAKGUARD 2>/dev/null
    ip6tables -D OUTPUT -j OUC_LEAKGUARD 2>/dev/null; ip6tables -F OUC_LEAKGUARD 2>/dev/null; ip6tables -X OUC_LEAKGUARD 2>/dev/null
    # A proxy bridge started by an earlier run lives in its own session and can
    # outlive both the supervisor and the state dir that recorded its pid.
    # Match our installed copy precisely; never pkill a bare "ouc".
    pkill -f "$LIB_DIR/ouc.py bridge" 2>/dev/null || true
    sleep 1
    pkill -9 -f "$LIB_DIR/ouc.py bridge" 2>/dev/null || true
    rm -f /etc/sudoers.d/opencode-unlimited
    rm -rf "$LIB_DIR" "$BIN_PATH" "$WARP_WRAPPER"
    rm -rf "$STATE_DIR"
  fi
fi

# final sweep: these must not survive under any circumstance
# A bridge whose binary we just deleted still shows that path in its cmdline,
# so this matches even after the removal above.
pkill -f "$LIB_DIR/ouc.py bridge" 2>/dev/null || true
rm -f /etc/sudoers.d/opencode-unlimited 2>/dev/null
rm -f /usr/local/bin/opencode-unlimited-keeper 2>/dev/null
rm -rf "$LIB_DIR" 2>/dev/null
rm -f "$WARP_WRAPPER" 2>/dev/null
rm -rf /tmp/opencode-unlimited 2>/dev/null

say "checking that the network is not firewalled..."
remaining=$(iptables -S OUTPUT 2>/dev/null | grep -c OUC_LEAKGUARD || true)
if [ "${remaining:-0}" = "0" ]; then
  ok "no leak-guard rules left"
else
  iptables -D OUTPUT -j OUC_LEAKGUARD 2>/dev/null || true
  ok "removed leftover leak-guard rules"
fi
ip6tables -D OUTPUT -j OUC_LEAKGUARD 2>/dev/null || true

echo ""
ok "uninstalled. opencode itself, your sessions and cloudflare-warp are untouched."
echo ""
