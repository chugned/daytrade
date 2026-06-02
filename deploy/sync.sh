#!/usr/bin/env bash
# Push code changes into the live ~/daytrade deployment and restart it.
#
# Like nighttrade, the live services run from ~/daytrade because macOS will
# NOT let launchd background services read ~/Desktop (TCC). Edit in the dev
# checkout (~/Desktop/coding/daytrade), then run this to deploy.
#
# State (artifacts/, data/, logs/) is EXCLUDED from the rsync — the deployed
# dir owns its observatory.db, models, and locks. The one-time state
# migration was done at first install; never let a code sync clobber it.
#
# Usage:  deploy/sync.sh [source-dir] [port]
#         source-dir defaults to ~/Desktop/coding/daytrade, port to 8000
set -euo pipefail

SRC="${1:-$HOME/Desktop/coding/daytrade}"
DEST="$HOME/daytrade"
PORT="${2:-8000}"

if [ ! -d "$SRC/src/daytrade" ]; then
  echo "ERROR: $SRC does not look like a daytrade checkout." >&2
  exit 1
fi

# deploy/_svc-run.sh is a machine-specific generated wrapper — never sync it,
# or a stale copy could repoint services at the wrong path.
rsync -a --delete \
  --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='.pytest_cache' --exclude='logs' --exclude='artifacts' \
  --exclude='data' --exclude='reports' --exclude='*.egg-info' \
  --exclude='deploy/_svc-run.sh' \
  "$SRC/" "$DEST/"
echo "synced $SRC -> $DEST"

# Reinstall — regenerates the wrapper + plists for THIS machine, reloads,
# and kickstarts (RunAtLoad is unreliable in the gui/ domain).
"$DEST/deploy/install.sh" "$PORT"

# Health check — learn must show a PID, dashboard must bind :$PORT. Surface
# failures loudly now, not next morning.
sleep 3
fail=0
if ! pgrep -f 'daytrade learn' >/dev/null; then
  echo "  WARN: learn observer did not start" >&2
  fail=1
fi
if ! lsof -i :"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "  WARN: dashboard not bound on :$PORT" >&2
  fail=1
fi
[ "$fail" -eq 0 ] && echo "  health: learn + dashboard both up"
