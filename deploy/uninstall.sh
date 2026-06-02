#!/usr/bin/env bash
# Stop and remove the daytrade launchd services installed by install.sh.
set -euo pipefail

AGENTS="$HOME/Library/LaunchAgents"
U="$(id -u)"

for label in com.daytrade.learn com.daytrade.dashboard com.daytrade.watchdog com.daytrade.caffeinate; do
  launchctl bootout "gui/$U/$label" 2>/dev/null || true
  rm -f "$AGENTS/$label.plist"
  echo "  removed $label"
done
echo "Done. daytrade services stopped and unloaded."
