#!/usr/bin/env bash
#
# daytrade — VPS provisioning script (Debian / Ubuntu).
#
# Bootstraps a fresh VPS into a hardened, supervised daytrade host:
#   - non-root 'daytrade' user
#   - Python 3.11 venv with project deps
#   - systemd units for learn + dashboard (auto-restart)
#   - UFW firewall (SSH only by default; Tailscale opt-in)
#   - basic hardening (no password root SSH)
#
# Idempotent: running it twice is safe.
#
# Usage (as root on a fresh VPS):
#     curl -fsSL https://your-host/provision-vps.sh | bash -s -- <github-repo-url>
# OR copy and run locally:
#     scp deploy/provision-vps.sh root@vps:/root/
#     ssh root@vps "bash /root/provision-vps.sh git@github.com:you/daytrade.git"
#
# Paper / simulation only — this script does NOT configure API keys, does
# NOT enable live trading. Live execution is a separate, deliberate step.

set -euo pipefail

REPO_URL="${1:-}"
DEPLOY_USER="daytrade"
DEPLOY_DIR="/opt/daytrade"
PYTHON_BIN="${PYTHON_BIN:-python3.11}"

if [[ -z "$REPO_URL" ]]; then
    echo "usage: $0 <git-clone-url>" >&2
    exit 1
fi
if [[ "$(id -u)" -ne 0 ]]; then
    echo "must run as root" >&2
    exit 1
fi

step() { echo -e "\n\033[1m== $* ==\033[0m"; }

# --- 1. Base packages -------------------------------------------------------
step "Updating package index + installing base packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
    git curl ca-certificates ufw \
    "$PYTHON_BIN" "${PYTHON_BIN}-venv" "${PYTHON_BIN}-dev" \
    build-essential

# --- 2. Deploy user ---------------------------------------------------------
step "Creating '$DEPLOY_USER' user (idempotent)"
if ! id -u "$DEPLOY_USER" >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash "$DEPLOY_USER"
fi

# --- 3. Clone / update repo -------------------------------------------------
step "Cloning / updating $REPO_URL into $DEPLOY_DIR"
if [[ ! -d "$DEPLOY_DIR/.git" ]]; then
    sudo -u "$DEPLOY_USER" git clone "$REPO_URL" "$DEPLOY_DIR"
else
    sudo -u "$DEPLOY_USER" git -C "$DEPLOY_DIR" pull --ff-only
fi

# --- 4. Python venv + deps --------------------------------------------------
step "Creating venv + installing requirements"
sudo -u "$DEPLOY_USER" "$PYTHON_BIN" -m venv "$DEPLOY_DIR/.venv"
sudo -u "$DEPLOY_USER" "$DEPLOY_DIR/.venv/bin/pip" install --quiet --upgrade pip
sudo -u "$DEPLOY_USER" "$DEPLOY_DIR/.venv/bin/pip" install --quiet -e "$DEPLOY_DIR[dev]" || \
    sudo -u "$DEPLOY_USER" "$DEPLOY_DIR/.venv/bin/pip" install --quiet -e "$DEPLOY_DIR"

# --- 5. Firewall ------------------------------------------------------------
step "Configuring UFW firewall (SSH only by default)"
ufw --force reset >/dev/null
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
# Dashboard intentionally NOT opened to the public internet.
# Pair this VPS with Tailscale for safe remote dashboard access:
#   curl -fsSL https://tailscale.com/install.sh | sh && tailscale up
ufw --force enable

# --- 6. Systemd units -------------------------------------------------------
step "Installing systemd units"
install -m 644 "$DEPLOY_DIR/deploy/systemd/daytrade-learn.service" \
    /etc/systemd/system/
install -m 644 "$DEPLOY_DIR/deploy/systemd/daytrade-dashboard.service" \
    /etc/systemd/system/
systemctl daemon-reload
systemctl enable daytrade-learn daytrade-dashboard
# Don't start yet — the operator should review the strategy/config first.
echo "   (services enabled; not started — review then 'systemctl start daytrade-learn daytrade-dashboard')"

# --- 7. Hardening -----------------------------------------------------------
step "Basic SSH hardening"
if grep -q "^#*PermitRootLogin" /etc/ssh/sshd_config; then
    sed -i 's/^#*PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
fi
if grep -q "^#*PasswordAuthentication" /etc/ssh/sshd_config; then
    sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
fi
systemctl reload ssh || systemctl reload sshd || true

# --- 8. Final summary -------------------------------------------------------
step "Provisioning complete"
cat <<EOF

  Deploy user:      $DEPLOY_USER
  Deploy path:      $DEPLOY_DIR
  Python:           $("$DEPLOY_DIR/.venv/bin/python" --version 2>&1)
  Systemd services: daytrade-learn, daytrade-dashboard (enabled, not running)

  Next steps:
    1. (Recommended) install Tailscale for safe dashboard access:
         curl -fsSL https://tailscale.com/install.sh | sh
         tailscale up
    2. Review configs/default.yaml + configs/watchlist.yaml on this host.
    3. Start the services:
         systemctl start daytrade-learn daytrade-dashboard
    4. Verify they're up:
         systemctl status daytrade-learn daytrade-dashboard
         journalctl -u daytrade-learn -f

  Paper / simulation only. Live trading requires a separate, deliberate
  step (trade-only API keys, see daytrade.ops.api_keys) — none of that is
  configured here.

EOF
