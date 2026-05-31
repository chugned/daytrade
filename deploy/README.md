# Deployment — process supervision

The daytrade processes (bot + dashboard) should be supervised so they
auto-restart on crash. Two recipes shipped:

| Platform | Files | How |
|---|---|---|
| **macOS** (your laptop) | `launchd/com.daytrade.learn.plist`, `launchd/com.daytrade.dashboard.plist` | per-user launchd agents |
| **VPS / Linux** | `systemd/daytrade-learn.service`, `systemd/daytrade-dashboard.service` | systemd units |

Why supervise? Both daytrade processes have died unexpectedly in the past
(the dashboard process twice). The bot itself was always intact, but the
dashboard cannot self-heal. A supervisor brings it back within seconds.

## macOS — launchd

```bash
# Edit WorkingDirectory in BOTH plists to point at your repo, then:
mkdir -p ~/Library/LaunchAgents logs
cp deploy/launchd/com.daytrade.learn.plist     ~/Library/LaunchAgents/
cp deploy/launchd/com.daytrade.dashboard.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/com.daytrade.learn.plist
launchctl load -w ~/Library/LaunchAgents/com.daytrade.dashboard.plist

# Check:
launchctl list | grep daytrade

# Stop / reload:
launchctl unload ~/Library/LaunchAgents/com.daytrade.dashboard.plist
launchctl load   ~/Library/LaunchAgents/com.daytrade.dashboard.plist
```

The single-instance lock in `daytrade.ops.SingleInstanceLock` means a
launchd-restarted bot can't accidentally run alongside a manually-started
one — the second to start refuses.

## Linux VPS — systemd

```bash
# As root on the VPS, after provisioning:
sudo cp deploy/systemd/daytrade-learn.service     /etc/systemd/system/
sudo cp deploy/systemd/daytrade-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now daytrade-learn daytrade-dashboard

# Status + logs:
systemctl status daytrade-learn daytrade-dashboard
journalctl -u daytrade-learn -f
```

## Both platforms

- The supervisor restarts the process on **any non-zero exit** with a
  10-second cooldown to avoid crash loops.
- A clean `Ctrl+C` exits the process; the supervisor will bring it back —
  use `launchctl unload` / `systemctl stop` to actually stop.
- Push notifications via Telegram / ntfy (see `daytrade.ops.notify`) will
  fire on trade events; pair them with the supervisor so a long-running
  silence is itself a signal.
