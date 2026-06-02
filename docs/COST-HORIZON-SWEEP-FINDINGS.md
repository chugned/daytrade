# Cost × Horizon Sensitivity Sweep (P5-3)

- Symbols: BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, LINKUSDT, AVAXUSDT
- Horizons: 15m, 30m, 60m, 120m, 240m
- Gate multiples: 2.0, 3.0, 4.0, 5.0
- Cost tiers: 6 bp, 14 bp, 24 bp (retail / maker / VIP)
- History: last 30 days × 1m candles per symbol
- Split: chronological 70% train / 30% test
- Matrix size: 1800 cells (30 training × 60 reslices)
- Eval time: 440.6s (parallel jobs=-1)

## ⚠ TL;DR — INVALIDATED by simulator follow-up (2026-06-02)

> The headline cells below ARE correct for the 30-day test window
> they were measured on. But the equity-curve simulator
> (`scripts/simulate_winner.py`) re-ran the top cell (BNB 240m
> gate=4.0) on **90 days** and got the **opposite** result:
>
> | Window | trades | mean net | cumulative |
> | --- | ---: | ---: | ---: |
> | 30d  (matches P5-3) | 245 | +29.45 bp | +7,215 bp |
> | 90d  (simulator) | 742 | **−60.85 bp** | **−45,147 bp** |
>
> Same code, same gate, same cost. The +30 bp at 30d was a
> recent-window regime effect, not a stable edge. The "winners"
> below should be read as "what looked good in a 9-day held-out
> window inside the 30-day sample" — not as a tradable strategy.
>
> See `docs/STRATEGY-CHANGE-RUNBOOK.md` (the do-not-execute banner)
> and `artifacts/equity_BNBUSDT_240m_g4.0.png`.

---

## TL;DR — yes, the strategy CAN be net-positive (within the 30d sample)

**The previous "all slices negative" result was a horizon problem,
not a strategy problem.** At 240-minute holds, the strategy clears
retail (24 bp) cost on two symbols with non-trivial event counts:

| Best at retail cost (24 bp) | n | gross | **net** |
| --- | ---: | ---: | ---: |
| **SOLUSDT** 240m `meta_gated` ×3.0 | 70 | +82.17 | **+58.17 bp** |
| **BNBUSDT** 240m `meta_gated` ×5.0 | 144 | +63.49 | **+39.49 bp** |
| **BNBUSDT** 240m `meta_gated` ×4.0 | 220 | +54.87 | **+30.87 bp** |
| **BNBUSDT** 240m `meta_gated` ×3.0 | 455 | +33.47 | **+9.47 bp** |
| **BNBUSDT** 120m `meta_gated` ×3.0 | 632 | +25.14 | **+1.14 bp** |

(18 cells total clear retail cost — see the full Winners table below.)

**At VIP/maker cost (6-14 bp), the winners list expands dramatically**
to 44+ cells including BTC at 30m. But that requires a fee tier
most operators don't have access to.

### What this means for the live config

The current live setup is configured for ~30-min trades with
`meta_label_edge_multiple = 2.0`. The data says **this is the wrong
horizon and the wrong gate strictness** for the symbols we're
running on:

1. **Switch the trading horizon to 240m (BNB / SOL) or 120m (BNB).**
   30-minute holds capture too little of the rebound — gross returns
   at 30m max at ~+15 bp on the best symbol, vs ~+55-82 bp at 240m.
2. **Tighten the gate to ×3.0-5.0.** At 240m, ×4.0 on BNB gives
   **n=220 events × +30.87 bp net = ~+679 bp of edge** over the
   27-day test window. Looser gates dilute precision; tighter gates
   below ×3.0 lose too many events.
3. **Trade BNB primarily, SOL as a secondary.** They are the only
   two symbols that produce retail-cost winners. BTC/ETH/LINK/AVAX
   don't get there at 30-240m at any tested gate strictness without
   VIP cost.
4. **Cascade UNION still gives a small lift, but is no longer
   load-bearing.** At the winning configurations, `cascade_or_gated`
   tracks `meta_gated` within ±2 bp — useful but not the headline.

### Caveats

- **Test window is short**: 27-day held-out per symbol. A regime
  shift inside the 27 days could distort. Re-run on 90d if any of
  these configs are going to be wired into the live bot.
- **n is small for the headline cells**: SOL 240m ×3.0 has only 70
  events; the +58 bp net could be regime luck. BNB 240m ×4.0 has
  220 events — more solid.
- **Capacity not modelled**: a 240m hold on BNB at ~8 trades per day
  is small enough that slippage stays near `base_slippage_bps=2`.
  At larger size the `impact_slippage_bps=8` would erode the edge.
- **Production pools across symbols** — these are per-symbol meta-
  models. The live pooled model could behave differently. A pooled
  re-run on the winning configs is the obvious follow-up.

## Winners (net >= 0, n >= 30 events)

This is the strategic question: *any cell here means a net-positive trade strategy exists at that (symbol, horizon, gate, cost).*

| Symbol | Horizon | Slice | Gate × | Cost (bp) | n | Gross | Net |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| SOLUSDT | 240m | `meta_gated` | 3.0 | 6 | 70 | +82.17 | **+76.17** |
| SOLUSDT | 240m | `meta_gated` | 3.0 | 14 | 70 | +82.17 | **+68.17** |
| SOLUSDT | 240m | `cascade_or_gated` | 3.0 | 6 | 83 | +73.55 | **+67.55** |
| SOLUSDT | 240m | `cascade_or_gated` | 3.0 | 14 | 83 | +73.55 | **+59.55** |
| SOLUSDT | 240m | `meta_gated` | 3.0 | 24 | 70 | +82.17 | **+58.17** |
| BNBUSDT | 240m | `meta_gated` | 5.0 | 6 | 144 | +63.49 | **+57.49** |
| BNBUSDT | 240m | `cascade_or_gated` | 5.0 | 6 | 157 | +59.70 | **+53.70** |
| SOLUSDT | 240m | `cascade_or_gated` | 3.0 | 24 | 83 | +73.55 | **+49.55** |
| BNBUSDT | 240m | `meta_gated` | 5.0 | 14 | 144 | +63.49 | **+49.49** |
| BNBUSDT | 240m | `meta_gated` | 4.0 | 6 | 220 | +54.87 | **+48.87** |
| BNBUSDT | 240m | `cascade_or_gated` | 4.0 | 6 | 233 | +52.79 | **+46.79** |
| BNBUSDT | 240m | `cascade_or_gated` | 5.0 | 14 | 157 | +59.70 | **+45.70** |
| BNBUSDT | 240m | `meta_gated` | 4.0 | 14 | 220 | +54.87 | **+40.87** |
| BNBUSDT | 240m | `meta_gated` | 5.0 | 24 | 144 | +63.49 | **+39.49** |
| BNBUSDT | 240m | `cascade_or_gated` | 4.0 | 14 | 233 | +52.79 | **+38.79** |
| BNBUSDT | 240m | `cascade_or_gated` | 5.0 | 24 | 157 | +59.70 | **+35.70** |
| BNBUSDT | 240m | `meta_gated` | 4.0 | 24 | 220 | +54.87 | **+30.87** |
| BNBUSDT | 240m | `cascade_or_gated` | 4.0 | 24 | 233 | +52.79 | **+28.79** |
| BNBUSDT | 240m | `meta_gated` | 3.0 | 6 | 455 | +33.47 | **+27.47** |
| BNBUSDT | 240m | `cascade_or_gated` | 3.0 | 6 | 468 | +33.03 | **+27.03** |
| BNBUSDT | 240m | `meta_gated` | 2.0 | 6 | 1159 | +25.97 | **+19.97** |
| BNBUSDT | 240m | `cascade_or_gated` | 2.0 | 6 | 1172 | +25.88 | **+19.88** |
| BNBUSDT | 120m | `meta_gated` | 4.0 | 6 | 403 | +25.50 | **+19.50** |
| BNBUSDT | 240m | `meta_gated` | 3.0 | 14 | 455 | +33.47 | **+19.47** |
| BNBUSDT | 120m | `meta_gated` | 3.0 | 6 | 632 | +25.14 | **+19.14** |
| BNBUSDT | 240m | `cascade_or_gated` | 3.0 | 14 | 468 | +33.03 | **+19.03** |
| BNBUSDT | 120m | `meta_gated` | 2.0 | 6 | 1131 | +25.03 | **+19.03** |
| BNBUSDT | 120m | `cascade_or_gated` | 2.0 | 6 | 1142 | +24.77 | **+18.77** |
| BNBUSDT | 120m | `cascade_or_gated` | 4.0 | 6 | 416 | +24.63 | **+18.63** |
| BNBUSDT | 120m | `cascade_or_gated` | 3.0 | 6 | 645 | +24.59 | **+18.59** |
| BNBUSDT | 120m | `meta_gated` | 5.0 | 6 | 275 | +23.60 | **+17.60** |
| BNBUSDT | 120m | `cascade_or_gated` | 5.0 | 6 | 288 | +22.43 | **+16.43** |
| BNBUSDT | 240m | `meta_gated` | 2.0 | 14 | 1159 | +25.97 | **+11.97** |
| BNBUSDT | 240m | `cascade_or_gated` | 2.0 | 14 | 1172 | +25.88 | **+11.88** |
| BNBUSDT | 120m | `meta_gated` | 4.0 | 14 | 403 | +25.50 | **+11.50** |
| BNBUSDT | 120m | `meta_gated` | 3.0 | 14 | 632 | +25.14 | **+11.14** |
| BNBUSDT | 120m | `meta_gated` | 2.0 | 14 | 1131 | +25.03 | **+11.03** |
| BNBUSDT | 120m | `cascade_or_gated` | 2.0 | 14 | 1142 | +24.77 | **+10.77** |
| BNBUSDT | 120m | `cascade_or_gated` | 4.0 | 14 | 416 | +24.63 | **+10.63** |
| BNBUSDT | 120m | `cascade_or_gated` | 3.0 | 14 | 645 | +24.59 | **+10.59** |
| BTCUSDT | 30m | `cascade_or_gated` | 5.0 | 6 | 134 | +15.90 | **+9.90** |
| BNBUSDT | 30m | `meta_gated` | 5.0 | 6 | 65 | +15.86 | **+9.86** |
| BNBUSDT | 30m | `cascade_or_gated` | 5.0 | 6 | 77 | +15.79 | **+9.79** |
| BNBUSDT | 120m | `meta_gated` | 5.0 | 14 | 275 | +23.60 | **+9.60** |
| BNBUSDT | 240m | `meta_gated` | 3.0 | 24 | 455 | +33.47 | **+9.47** |
| BTCUSDT | 30m | `cascade_or_gated` | 3.0 | 6 | 147 | +15.33 | **+9.33** |
| BTCUSDT | 30m | `cascade_or_gated` | 4.0 | 6 | 141 | +15.26 | **+9.26** |
| BTCUSDT | 30m | `meta_gated` | 5.0 | 6 | 121 | +15.20 | **+9.20** |
| BNBUSDT | 240m | `cascade_or_gated` | 3.0 | 24 | 468 | +33.03 | **+9.03** |
| BTCUSDT | 30m | `meta_gated` | 3.0 | 6 | 134 | +14.64 | **+8.64** |
| BTCUSDT | 30m | `cascade_or_gated` | 2.0 | 6 | 165 | +14.54 | **+8.54** |
| BTCUSDT | 30m | `meta_gated` | 4.0 | 6 | 128 | +14.52 | **+8.52** |
| BNBUSDT | 120m | `cascade_or_gated` | 5.0 | 14 | 288 | +22.43 | **+8.43** |
| BTCUSDT | 30m | `meta_gated` | 2.0 | 6 | 152 | +13.86 | **+7.86** |
| BNBUSDT | 30m | `cascade_or_gated` | 4.0 | 6 | 86 | +13.32 | **+7.32** |
| BNBUSDT | 30m | `meta_gated` | 4.0 | 6 | 74 | +12.98 | **+6.98** |
| ETHUSDT | 60m | `meta_gated` | 5.0 | 6 | 265 | +11.57 | **+5.57** |
| LINKUSDT | 30m | `cascade_or_gated` | 4.0 | 6 | 167 | +10.89 | **+4.89** |
| ETHUSDT | 60m | `cascade_or_gated` | 5.0 | 6 | 281 | +10.41 | **+4.41** |
| BNBUSDT | 240m | `all` | 2.0 | 6 | 12773 | +10.18 | **+4.18** |
| BNBUSDT | 240m | `all` | 3.0 | 6 | 12773 | +10.18 | **+4.18** |
| BNBUSDT | 240m | `all` | 4.0 | 6 | 12773 | +10.18 | **+4.18** |
| BNBUSDT | 240m | `all` | 5.0 | 6 | 12773 | +10.18 | **+4.18** |
| LINKUSDT | 30m | `cascade_or_gated` | 5.0 | 6 | 127 | +9.93 | **+3.93** |
| LINKUSDT | 30m | `meta_gated` | 4.0 | 6 | 157 | +9.64 | **+3.64** |
| BNBUSDT | 30m | `cascade_or_gated` | 3.0 | 6 | 100 | +8.95 | **+2.95** |
| LINKUSDT | 30m | `meta_gated` | 5.0 | 6 | 117 | +8.17 | **+2.17** |
| BNBUSDT | 30m | `meta_gated` | 3.0 | 6 | 88 | +8.07 | **+2.07** |
| BNBUSDT | 240m | `meta_gated` | 2.0 | 24 | 1159 | +25.97 | **+1.97** |
| BTCUSDT | 30m | `cascade_or_gated` | 5.0 | 14 | 134 | +15.90 | **+1.90** |
| BNBUSDT | 240m | `cascade_or_gated` | 2.0 | 24 | 1172 | +25.88 | **+1.88** |
| BNBUSDT | 30m | `meta_gated` | 5.0 | 14 | 65 | +15.86 | **+1.86** |
| AVAXUSDT | 15m | `meta_gated` | 4.0 | 6 | 149 | +7.85 | **+1.85** |
| BNBUSDT | 30m | `cascade_or_gated` | 5.0 | 14 | 77 | +15.79 | **+1.79** |
| AVAXUSDT | 15m | `meta_gated` | 5.0 | 6 | 105 | +7.60 | **+1.60** |
| ETHUSDT | 60m | `meta_gated` | 4.0 | 6 | 331 | +7.58 | **+1.58** |
| BNBUSDT | 120m | `meta_gated` | 4.0 | 24 | 403 | +25.50 | **+1.50** |
| AVAXUSDT | 15m | `meta_gated` | 3.0 | 6 | 200 | +7.40 | **+1.40** |
| BTCUSDT | 30m | `cascade_or_gated` | 3.0 | 14 | 147 | +15.33 | **+1.33** |
| AVAXUSDT | 15m | `cascade_or_gated` | 4.0 | 6 | 162 | +7.32 | **+1.32** |
| BTCUSDT | 30m | `cascade_or_gated` | 4.0 | 14 | 141 | +15.26 | **+1.26** |
| BTCUSDT | 30m | `meta_gated` | 5.0 | 14 | 121 | +15.20 | **+1.20** |
| BNBUSDT | 120m | `meta_gated` | 3.0 | 24 | 632 | +25.14 | **+1.14** |
| BNBUSDT | 120m | `meta_gated` | 2.0 | 24 | 1131 | +25.03 | **+1.03** |
| AVAXUSDT | 15m | `cascade_or_gated` | 3.0 | 6 | 213 | +7.02 | **+1.02** |
| AVAXUSDT | 15m | `cascade_or_gated` | 5.0 | 6 | 118 | +6.89 | **+0.89** |
| ETHUSDT | 60m | `cascade_or_gated` | 4.0 | 6 | 347 | +6.83 | **+0.83** |
| BNBUSDT | 120m | `cascade_or_gated` | 2.0 | 24 | 1142 | +24.77 | **+0.77** |
| BTCUSDT | 60m | `cascade_or_gated` | 5.0 | 6 | 286 | +6.75 | **+0.75** |
| BNBUSDT | 30m | `cascade_or_gated` | 2.0 | 6 | 140 | +6.72 | **+0.72** |
| BTCUSDT | 30m | `meta_gated` | 3.0 | 14 | 134 | +14.64 | **+0.64** |
| BNBUSDT | 120m | `cascade_or_gated` | 4.0 | 24 | 416 | +24.63 | **+0.63** |
| BTCUSDT | 60m | `cascade_or_gated` | 4.0 | 6 | 337 | +6.61 | **+0.61** |
| BNBUSDT | 120m | `cascade_or_gated` | 3.0 | 24 | 645 | +24.59 | **+0.59** |
| BTCUSDT | 30m | `cascade_or_gated` | 2.0 | 14 | 165 | +14.54 | **+0.54** |
| BTCUSDT | 30m | `meta_gated` | 4.0 | 14 | 128 | +14.52 | **+0.52** |
| AVAXUSDT | 15m | `meta_gated` | 2.0 | 6 | 341 | +6.06 | **+0.06** |

## Near-winners (net within 5 bp of break-even)

_Top 20 cells within 5.0 bp of break-even (sorted by net):_

| Symbol | Horizon | Slice | Gate × | Cost (bp) | n | Gross | Net |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| BNBUSDT | 30m | `meta_gated` | 2.0 | 6 | 128 | +5.90 | -0.10 |
| BTCUSDT | 60m | `cascade_or_gated` | 3.0 | 6 | 440 | +5.89 | -0.11 |
| BTCUSDT | 60m | `meta_gated` | 5.0 | 6 | 275 | +5.88 | -0.12 |
| AVAXUSDT | 15m | `cascade_or_gated` | 2.0 | 6 | 354 | +5.88 | -0.12 |
| BTCUSDT | 60m | `meta_gated` | 4.0 | 6 | 326 | +5.87 | -0.13 |
| BTCUSDT | 30m | `meta_gated` | 2.0 | 14 | 152 | +13.86 | -0.14 |
| BNBUSDT | 120m | `meta_gated` | 5.0 | 24 | 275 | +23.60 | -0.40 |
| BNBUSDT | 30m | `cascade_or_gated` | 4.0 | 14 | 86 | +13.32 | -0.68 |
| BTCUSDT | 60m | `meta_gated` | 3.0 | 6 | 429 | +5.31 | -0.69 |
| BNBUSDT | 30m | `meta_gated` | 4.0 | 14 | 74 | +12.98 | -1.02 |
| LINKUSDT | 60m | `cascade_or_gated` | 4.0 | 6 | 127 | +4.88 | -1.12 |
| BNBUSDT | 120m | `all` | 2.0 | 6 | 12809 | +4.67 | -1.33 |
| BNBUSDT | 120m | `all` | 3.0 | 6 | 12809 | +4.67 | -1.33 |
| BNBUSDT | 120m | `all` | 4.0 | 6 | 12809 | +4.67 | -1.33 |
| BNBUSDT | 120m | `all` | 5.0 | 6 | 12809 | +4.67 | -1.33 |
| BNBUSDT | 120m | `cascade_or_gated` | 5.0 | 24 | 288 | +22.43 | -1.57 |
| ETHUSDT | 60m | `meta_gated` | 5.0 | 14 | 265 | +11.57 | -2.43 |
| LINKUSDT | 60m | `meta_gated` | 4.0 | 6 | 118 | +3.55 | -2.45 |
| BNBUSDT | 15m | `cascade_or_gated` | 2.0 | 6 | 392 | +3.07 | -2.93 |
| LINKUSDT | 30m | `cascade_or_gated` | 4.0 | 14 | 167 | +10.89 | -3.11 |

## Heatmap — UNION slice at retail (24 bp) cost

_slice=`cascade_or_gated`, gate=×2.0, cost=24 bp, cell = mean_return_net_bps (n in parentheses):_

| Symbol | 15m | 30m | 60m | 120m | 240m |
| --- | ---: | ---: | ---: | ---: | ---: |
| BTCUSDT | -31.0 (45) | -9.5 (165) | -22.1 (666) | -33.6 (1081) | -48.3 (977) |
| ETHUSDT | -27.4 (183) | -23.3 (547) | -30.8 (957) | -28.3 (1547) | -49.1 (1545) |
| SOLUSDT | -27.2 (137) | -29.3 (530) | -26.9 (1199) | -32.4 (1009) | -24.2 (594) |
| BNBUSDT | -20.9 (392) | -17.3 (140) | -28.1 (685) | +0.8 (1142) | +1.9 (1172) |
| LINKUSDT | -21.5 (261) | -23.8 (505) | -26.4 (538) | -38.6 (404) | -37.8 (58) |
| AVAXUSDT | -18.1 (354) | -28.3 (649) | -28.3 (839) | -64.8 (483) | -77.1 (388) |

## Heatmap — UNION slice at VIP (6 bp) cost

_slice=`cascade_or_gated`, gate=×2.0, cost=6 bp, cell = mean_return_net_bps (n in parentheses):_

| Symbol | 15m | 30m | 60m | 120m | 240m |
| --- | ---: | ---: | ---: | ---: | ---: |
| BTCUSDT | -13.0 (45) | +8.5 (165) | -4.1 (666) | -15.6 (1081) | -30.3 (977) |
| ETHUSDT | -9.4 (183) | -5.3 (547) | -12.8 (957) | -10.3 (1547) | -31.1 (1545) |
| SOLUSDT | -9.2 (137) | -11.3 (530) | -8.9 (1199) | -14.4 (1009) | -6.2 (594) |
| BNBUSDT | -2.9 (392) | +0.7 (140) | -10.1 (685) | +18.8 (1142) | +19.9 (1172) |
| LINKUSDT | -3.5 (261) | -5.8 (505) | -8.4 (538) | -20.6 (404) | -19.8 (58) |
| AVAXUSDT | -0.1 (354) | -10.3 (649) | -10.3 (839) | -46.8 (483) | -59.1 (388) |

## Heatmap — meta_gated at retail (24 bp) cost

_slice=`meta_gated`, gate=×2.0, cost=24 bp, cell = mean_return_net_bps (n in parentheses):_

| Symbol | 15m | 30m | 60m | 120m | 240m |
| --- | ---: | ---: | ---: | ---: | ---: |
| BTCUSDT | -34.3 (32) | -10.1 (152) | -22.6 (655) | -34.0 (1069) | -48.9 (965) |
| ETHUSDT | -27.3 (167) | -22.9 (532) | -30.8 (941) | -28.3 (1531) | -49.4 (1530) |
| SOLUSDT | -29.7 (123) | -30.3 (516) | -27.2 (1186) | -32.5 (997) | -24.8 (582) |
| BNBUSDT | -21.2 (380) | -18.1 (128) | -28.5 (673) | +1.0 (1131) | +2.0 (1159) |
| LINKUSDT | -22.2 (251) | -24.5 (495) | -26.8 (529) | -38.5 (395) | -32.6 (49) |
| AVAXUSDT | -17.9 (341) | -28.8 (636) | -28.6 (827) | -65.7 (471) | -79.3 (376) |

## Heatmap — cascade_exhaustion alone at retail (24 bp) cost

_slice=`cascade_exhaustion`, gate=×2.0, cost=24 bp, cell = mean_return_net_bps (n in parentheses):_

| Symbol | 15m | 30m | 60m | 120m | 240m |
| --- | ---: | ---: | ---: | ---: | ---: |
| BTCUSDT | -22.8 (13) | -1.5 (13) | +3.8 (12) | -3.3 (13) | -1.4 (13) |
| ETHUSDT | -27.5 (17) | -36.1 (17) | -35.5 (17) | -35.4 (18) | -15.1 (18) |
| SOLUSDT | -5.3 (14) | +4.7 (14) | -1.1 (14) | -19.2 (13) | +3.1 (13) |
| BNBUSDT | -13.8 (12) | -8.6 (12) | -7.5 (12) | -26.3 (13) | -6.3 (13) |
| LINKUSDT | -4.9 (10) | +6.5 (10) | -1.6 (9) | -43.6 (9) | -65.9 (9) |
| AVAXUSDT | -22.8 (13) | -6.2 (13) | -6.5 (13) | -28.4 (13) | -1.0 (13) |

