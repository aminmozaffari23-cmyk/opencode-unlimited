#!/usr/bin/env bash
# Live setup for the v2.5.0 fallback backend test.
# Machine is disposable; nothing here is preserved.
set -x
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -qq
sudo apt-get install -y -qq curl jq python3 iproute2 procps 2>&1 | tail -3

# ---- Psiphon tunnel core ------------------------------------------------
sudo curl -fsSL --max-time 180 -o /usr/local/bin/psiphon-tunnel-core \
  https://github.com/Psiphon-Labs/psiphon-tunnel-core-binaries/raw/master/linux/psiphon-tunnel-core-x86_64
sudo chmod 755 /usr/local/bin/psiphon-tunnel-core
ls -la /usr/local/bin/psiphon-tunnel-core

# ---- WARP ---------------------------------------------------------------
if ! command -v warp-cli >/dev/null; then
  curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg \
    | sudo gpg --yes --dearmor -o /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg
  echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ noble main" \
    | sudo tee /etc/apt/sources.list.d/cloudflare-client.list >/dev/null
  sudo apt-get update -qq && sudo apt-get install -y -qq cloudflare-warp 2>&1 | tail -3
fi
sudo pkill -f "[w]arp-svc" 2>/dev/null; sleep 1
sudo nohup warp-svc >/tmp/warp-svc.log 2>&1 &
sleep 6
warp-cli --accept-tos registration new 2>&1 | tail -1
warp-cli --accept-tos mode proxy 2>&1 | tail -1
warp-cli --accept-tos connect 2>&1 | tail -1
sleep 8
warp-cli --accept-tos status 2>&1 | head -2

# ---- opencode -----------------------------------------------------------
if [ ! -x "$HOME/.opencode/bin/opencode" ]; then
  curl -fsSL https://opencode.ai/install | bash >/tmp/oc-install.log 2>&1 || tail -20 /tmp/oc-install.log
fi
export PATH="$HOME/.opencode/bin:$PATH"
opencode --version

# ---- the product at the commit under test ------------------------------
sudo mkdir -p /workspaces && sudo chown "$(id -u):$(id -g)" /workspaces
if [ ! -d /workspaces/opencode-unlimited/.git ]; then
  git clone -q https://github.com/aminmozaffari23-cmyk/opencode-unlimited.git \
      /workspaces/opencode-unlimited
fi
cd /workspaces/opencode-unlimited
git fetch -q origin main && git checkout -q main && git reset -q --hard origin/main
git log --oneline -1
bash install.sh --proxy --portable >/tmp/install.log 2>&1; echo "install rc=$?"
tail -6 /tmp/install.log

# The installed supervisor holds the managed-run lease (by design) and cron
# restarts it every two minutes. For a hands-free *rotation* test the supervisor
# is not what we are exercising, so stop both and keep them stopped.
crontab -l 2>/dev/null | grep -v "ouc " > /tmp/cron.new 2>/dev/null || true
crontab /tmp/cron.new 2>/dev/null || true
python3 ouc.py stop >/dev/null 2>&1 || true
sudo pkill -f "[o]uc.py watchdog" 2>/dev/null || true
sleep 1
ps aux | grep -c "[o]uc.py supervise"

mkdir -p /workspace/lab3/evidence
echo "SETUP-DONE"
