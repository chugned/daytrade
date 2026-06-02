#!/usr/bin/env bash
# Defence-in-depth watchdog for the daytrade services.
#
# launchd KeepAlive restarts a crashed job, but can park a job in throttle
# backoff after repeated rapid exits. This script is the belt-and-suspenders
# layer: every 5 min (via com.daytrade.watchdog StartInterval=300), verify
# both services are alive and kickstart them if not.
#
# Read-only against the bot's DB. Only side effect: launchctl kickstart on a
# down service. Logs to logs/watchdog.log.
set -euo pipefail

REPO="${DT_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LOG="$REPO/logs/watchdog.log"
PORT="${DT_DASHBOARD_PORT:-8000}"
U=$(id -u)

mkdir -p "$REPO/logs"

log() {
  printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG"
}

kick() {
  local svc="$1"
  log "  kickstart $svc"
  launchctl kickstart -k "gui/$U/$svc" >/dev/null 2>&1 || true
}

# Learn observer: is the Python process alive?
learn_alive() {
  pgrep -f 'daytrade learn' > /dev/null
}

# Dashboard: is the configured port bound by any process?
dashboard_bound() {
  lsof -i :"$PORT" -sTCP:LISTEN > /dev/null 2>&1
}

issues=0
if ! learn_alive; then
  log "WARN learn observer not running"
  kick com.daytrade.learn
  issues=$((issues + 1))
fi

if ! dashboard_bound; then
  log "WARN dashboard not bound on :$PORT"
  kick com.daytrade.dashboard
  issues=$((issues + 1))
fi

if [ "$issues" -eq 0 ]; then
  # Quiet success — only log every 10th tick so the log isn't spammed by
  # 288 successful checks per day.
  tick_marker="$REPO/data/.watchdog_quiet_count"
  count=$(cat "$tick_marker" 2>/dev/null || echo "0")
  count=$((count + 1))
  if [ "$count" -ge 10 ]; then
    log "ok learn + dashboard both healthy ($count quiet ticks)"
    echo "0" > "$tick_marker"
  else
    echo "$count" > "$tick_marker"
  fi
  exit 0
fi

# Wait briefly for kickstart to take effect, then re-check.
sleep 8
recovered=1
if ! learn_alive; then
  log "STILL DOWN: learn observer did not respawn after kickstart"
  recovered=0
fi
if ! dashboard_bound; then
  log "STILL DOWN: dashboard did not bind on :$PORT after kickstart"
  recovered=0
fi

if [ "$recovered" -eq 1 ]; then
  log "recovered after kickstart"
  exit 0
else
  log "ERROR not recovered — manual intervention required"
  exit 2
fi
