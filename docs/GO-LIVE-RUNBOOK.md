# Go-Live Runbook

The exact sequence from "paper-only today" to "real money in a Binance
subaccount". Every step is reversible until the very last one. **Do not
skip steps.** Each one exists because something specific can go wrong.

If you only read one section, read **§10 The Four Gates** — those are
the only commands that move real money.

---

## 1. Pre-flight (do these BEFORE touching Binance)

- [ ] Pull the latest `Secure` branch (LiveBroker scaffold is merged here):
      ```
      git checkout Secure && git pull
      ```
- [ ] Run the full test suite — must be green:
      ```
      python3 -m pytest
      # expect: 529 passed (or higher)
      ```
- [ ] Confirm your existing paper bot is still earning. The point of
      going live is to *capture* an edge that's already visible in
      paper. If the paper edge has died, **do not deploy live.**

## 2. Binance subaccount

Create a fresh subaccount specifically for the bot. **Do not use your
main account.** A subaccount has its own balance, its own API keys,
and a damage budget equal to whatever you transfer in.

- [ ] Log into Binance → User Center → Sub-Accounts → Create Sub-Account
- [ ] Name it something obvious (e.g. `daytrade-bot-shadow`).
- [ ] Note the email; you'll need it.
- [ ] (Recommended) Enable 2FA on the subaccount.

## 3. Fund the subaccount

The amount you transfer is the **maximum loss** if the bot blows up.
Treat it as risk capital.

- [ ] Transfer **€100-€200 of USDT** from your main account into the
      subaccount (Internal Transfer; instant, no fee).
- [ ] Do NOT transfer more. Scale up only after 30 days of clean live
      PnL.

## 4. Generate the trade-only API key

- [ ] In the subaccount → API Management → Create API.
- [ ] Name it `daytrade-shadow` so you can see what's calling.
- [ ] Permissions (this is the only line that matters):

      ✅ Enable Spot & Margin Trading
      ❌ Enable Withdrawals             ← MUST stay off
      ❌ Enable Internal Transfer       ← MUST stay off
      ❌ Permit Universal Transfer      ← MUST stay off
      ❌ Enable Futures (unless explicitly using)

- [ ] **IP-restrict the key.** Add the public IP of the host that will
      run the bot. Without this, a leaked key from any source can be
      used. With this, the key is useless off the trading host.

- [ ] Copy the API key and API secret — they're only shown once.

## 5. Pass the credentials to the bot

The bot reads them from environment variables; it never writes them
to disk.

- [ ] Set up a `.env` file (NOT committed to git):
      ```
      DAYTRADE_BINANCE_KEY=<your_key>
      DAYTRADE_BINANCE_SECRET=<your_secret>
      ```
- [ ] Confirm `.env` is in `.gitignore`.
- [ ] Source it before running:
      ```
      export $(grep -v '^#' .env | xargs)
      ```

## 6. Validate the key permissions

This is the only command in the runbook that makes a private API call
to Binance. It verifies the key is trade-only and crashes if it can
withdraw.

```bash
PYTHONPATH=src python3 -c "
import os
from daytrade.ops.api_keys import inspect_key, assert_trade_only
p = inspect_key(os.environ['DAYTRADE_BINANCE_KEY'], os.environ['DAYTRADE_BINANCE_SECRET'])
print(p)
assert_trade_only(p)
print('OK — trade-only key, safe to use.')
"
```

Expected output (key fields):
```
KeyPermissions(... can_trade=True can_withdraw=False can_internal_transfer=False ...)
OK — trade-only key, safe to use.
```

If you see `can_withdraw=True` anywhere, **stop**. Go to Binance and
disable withdrawals on the key. Then retry this step.

## 7. Run shadow mode for 7 days

This is the smoke test. Real Binance reads, fake order writes. No
money moves; you're proving the live wiring works end-to-end.

- [ ] Start the shadow runner:
      ```
      PYTHONPATH=src python3 -m daytrade.cli.main shadow --interval 300
      ```
- [ ] Verify the startup banner says:
      - `Binance reader online (writes disabled)`
      - `Shadow USDT balance: <real number>` (matches your subaccount)
      - `Orders are routed to the in-memory MockExchange. No real
        orders will be placed.`
- [ ] Let it run for **7 days**. Use `tmux` / `screen` / systemd so it
      survives terminal disconnects (`deploy/systemd/daytrade-shadow.service`
      template if you have one).

## 8. Compare shadow vs paper

After at least 24 hours of shadow runs (preferably 7 days):

```bash
PYTHONPATH=src python3 -m daytrade.cli.main shadow-compare
```

Reads both DBs (`artifacts/observatory.db` and
`artifacts/observatory-shadow.db`) and prints a side-by-side ledger.

Or in the FastAPI dashboard: `GET /api/shadow-vs-paper`.

**Acceptance criteria:**

- Shadow closed-trade count is within ±20% of paper's count over the
  same window.
- Shadow win rate is within ±10 percentage points of paper's win rate.
- Shadow PnL is within ±15% of paper PnL **after annualisation**.
- Zero `ExchangeUnreachable` errors that did not auto-recover.
- Zero reconciliation drift warnings in `logs/daytrade.log`.

If any criterion fails, **do not flip live gates**. Fix the root cause
and rerun shadow for another 7 days.

## 9. Plan your kill-switch BEFORE going live

Pick one and commit to it:

- **Hard drawdown:** `LiveConfig.max_daily_loss = 30` (already the
  default). The broker halts new BUYs for the rest of the UTC day if
  cumulative loss exceeds €30. You re-enable manually.
- **Manual:** be reachable for the first 7 days of live trading. Have
  a way to stop the bot from your phone (SSH + tmux works fine).
- **Both** is fine.

## 10. The Four Gates (the only real-money step)

This is the only point at which the bot can place real orders. Do this
deliberately, once, when everything in §8 has passed.

All four must happen in the same session:

### Gate 1 — `LiveConfig`

In your config or override, set:
```python
LiveConfig(
    dry_run=False,              # gate 1
    max_stake_per_trade=10.0,   # €10 for week 1, scale only after
    max_daily_loss=30.0,
    max_open_positions=3,
)
```

### Gate 2 — Enable writes on the exchange adapter

```python
exchange = BinanceExchange(...)
exchange.enable_writes()        # gate 2
```

The `enable_writes()` call logs a warning to make it visible. Don't
script it; type it.

### Gate 3 — `SafetyConfig` flag triple

In `config.yaml` (or wherever the safety section lives):
```yaml
safety:
  live_trading_enabled: true
  allow_real_orders: true
  paper_trading: false
  live_acknowledgement: "I understand this places real orders with real money on Binance and the maximum loss is the funded capital."
```

Note: the last line is the acknowledgement phrase. It must match
character-for-character. The validator refuses anything else.

### Gate 4 — Run with the live broker

```bash
PYTHONPATH=src python3 -m daytrade.cli.main observe \
  --profile live
```

(You'll need to wire a `live` profile that builds the live broker
chain; the shadow CLI already shows the pattern. Or build a new
`daytrade live` command — same shape as `shadow` but without the
`writes_enabled=False` line.)

## 11. Day-1 monitoring (do NOT skip this)

- [ ] Watch the first 5 live trades in person. Verify they actually
      fill on Binance.
- [ ] Check `daytrade shadow-compare` (or the dashboard) at 4-hour
      intervals for the first day.
- [ ] If drawdown hits €15 — pause and read the logs. Don't trade
      through the first sign of trouble.
- [ ] At end of day 1, write down: how many trades, win rate, PnL,
      any unexpected log lines.

## 12. Week 1 — €10 stake-per-trade cap

Keep `max_stake_per_trade=10` for the entire first week even if it
limits scaling. The goal of week 1 is operational, not financial:
prove the wiring works on real fills.

## 13. Scaling rules

Only raise `max_stake_per_trade` if all of these are true:

- 30 days of clean live operation.
- Live PnL after fees > 0 on a rolling 30-day basis.
- Reconciliation drift count = 0.
- You've successfully triggered the kill-switch at least once (in
  practice — flip the bot off manually and confirm everything stops
  cleanly).

Next step after week 1: `max_stake_per_trade = 25` for 2 weeks. Then
50. Then up to the maximum you're willing to risk on a single trade.

## 14. Reversing

At any point you can:

- **Pause:** Ctrl+C the bot. Open positions stay open. You can resume
  by restarting; the bot reloads its position state from the DB.
- **Close all positions:** flip the kill-switch (manually close in
  Binance UI, then restart). The bot's reconciliation will pick up
  the new state.
- **Withdraw capital:** main account → subaccount transfer is instant.
  You can pull funds out any time.

The four gates can be flipped back to paper any time by reverting the
config. The next observer restart will use the paper broker again.

## 15. What to do when you find a bug

- **Stop the bot.** Don't trade through unknown behaviour.
- **Take a snapshot:** `cp artifacts/observatory.db /tmp/bug-snapshot.db`
- **Read the logs:** `tail -200 logs/daytrade.log`
- **Reproduce in shadow** before fixing live.
- **Add a test** that would have caught the bug before merging.

## 16. Hard safety facts (memorise)

- **Withdraw-enabled keys are refused at startup.** This is the only
  reliable defence against credential theft. Never make an exception.
- **No leverage.** Spot only. Futures liquidations can wipe the
  account in one trade.
- **The funded amount IS the maximum loss.** Treat the subaccount
  balance as already gone. If you can't lose it, don't fund it.
- **Backtests are not reality.** Even shadow mode is not reality.
  Only live PnL is real PnL.

---

## Quick reference card

| Need to… | Command |
| --- | --- |
| Run paper bot (current production) | `daytrade observe` |
| Validate API key permissions | (the inspect_key Python one-liner in §6) |
| Run shadow mode (real reads, fake writes) | `daytrade shadow --interval 300` |
| Compare shadow vs paper ledgers | `daytrade shadow-compare` |
| View comparison in dashboard | `GET /api/shadow-vs-paper` |
| Live mode (after §10) | `daytrade observe --profile live` |
| Halt all trading | Ctrl+C the bot |
| Re-flatten everything | Manual close on Binance UI, then restart bot |
