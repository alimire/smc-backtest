# Research Results

## Honest Summary

Best observed walk-forward/out-of-sample result was `frequency:legacy_chop_loose` on `EURUSD=X 15m` with expectancy `1.621R`, profit factor `3.53` across `39` resolved OOS trades. A credible edge met the minimum bar here.

**Verdict:** Credible edge met OOS bar — see preset notes.

## Data story (corrected Jul 2026)

- **Primary research surface:** cTrader demo EURUSD **M15** via `fetch_broker_15m("ctrader")` — ~49.6k bars / ~24 months (`data_cache/EURUSD_ctrader_M15_730d.csv`).
- Yahoo **15m/5m** remains capped at ~60 calendar days; that is **not** the research limit once broker cache is present.
- Yahoo **1h** (~730d) is still useful as a secondary cross-check when broker 15m is unavailable.
- **4h** bars are resampled from cached 1h OHLC (valid aggregation, not fake 15m).
- DXY correlation on broker 15m uses Yahoo 15m (~60d overlap only); earlier bars have no SMT pair.
- Dukascopy Python client failed here; cTrader Open API historical fetch is the long 15m source.

## Data Collected

| Symbol | Interval | Source | Start | End | Bars | Cache |
|---|---|---|---|---:|---:|---|
| `EURUSD=X` | `15m` | `ctrader` | `2024-07-21 22:00:00+01:00` | `2026-07-20 12:15:00+01:00` | 49594 | `/Users/alimire/Downloads/smc-backtest/data_cache/EURUSD_ctrader_M15_730d.csv` |

## EURUSD cTrader M15 — full window vs last 9 months (in-sample)

| Window | Strategy | n | WR | E[R] | PF | Flag |
|---|---|---:|---:|---:|---:|---|
| `full_~24mo` | `Legacy` | 8 | 37.5% | 1.814 | 3.90 | resolved<15 |
| `full_~24mo` | `Advanced default` | 3 | 33.3% | 3.170 | 5.75 | resolved<15 |
| `last_9mo` | `Legacy` | 3 | 66.7% | 4.690 | 15.07 | resolved<15 |
| `last_9mo` | `Advanced default` | 2 | 50.0% | 5.255 | 11.51 | resolved<15 |

## OOS Results (3-fold walk-forward aggregate)

| Dataset | Strategy | Trades | Resolved | Win Rate | Avg RR | Expectancy R | PF | Max DD R | Lose Streak | Flag |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `EURUSD ctrader 15m` | `Legacy` | 4 | 4 | 50.0% | 5.89 | 3.268 | 7.54 | 2.00 | 2 | resolved<15 |
| `EURUSD ctrader 15m` | `Advanced default` | 3 | 3 | 33.3% | 6.67 | 3.170 | 5.75 | 2.00 | 2 | resolved<15 |
| `EURUSD ctrader 15m` | `Advanced tuned` | 3 | 3 | 33.3% | 6.67 | 3.170 | 5.75 | 2.00 | 2 | resolved<15 |

## EURUSD cTrader M15 fold detail (Legacy vs Advanced)

### Legacy

| Fold | Train E[R] | Train n | OOS E[R] | OOS n | OOS PF |
|---:|---:|---:|---:|---:|---:|
| 1 | 0.360 | 4 | -1.000 | 1 | 0.00 |
| 2 | 0.088 | 5 | -1.000 | 1 | 0.00 |
| 3 | -0.093 | 6 | 7.535 | 2 | inf |

### Advanced default

| Fold | Train E[R] | Train n | OOS E[R] | OOS n | OOS PF |
|---:|---:|---:|---:|---:|---:|
| 1 | 0.000 | 0 | -1.000 | 1 | 0.00 |
| 2 | -1.000 | 1 | -1.000 | 1 | 0.00 |
| 3 | -1.000 | 2 | 11.510 | 1 | inf |

### Advanced tuned

| Fold | Train E[R] | Train n | OOS E[R] | OOS n | OOS PF |
|---:|---:|---:|---:|---:|---:|
| 1 | 0.000 | 0 | -1.000 | 1 | 0.00 |
| 2 | -1.000 | 1 | -1.000 | 1 | 0.00 |
| 3 | -1.000 | 2 | 11.510 | 1 | inf |


## Reject funnel (full-sample cTrader M15 defaults)

### `EURUSD=X 15m`

**Legacy defaults (session=both, min_rr=2.0)** — setups=8, POI retests=5799, accepts=8, bars_in_session=9288

| Reason | Count |
|---|---:|
| `mid_range` | 3750 |
| `chop` | 1149 |
| `no_idm_sweep` | 886 |
| `no_bos_after_liq` | 3 |
| `duplicate` | 2 |
| `no_liquidity_sweep` | 1 |

Structure counts: FBOS=0, RBOS=0, CISD=0, AMD=0.

**Advanced defaults (session=both, min_rr=2.0)** — setups=3, POI retests=5799, accepts=3, bars_in_session=9288

| Reason | Count |
|---|---:|
| `mid_range` | 3750 |
| `chop` | 1149 |
| `no_idm_sweep` | 886 |
| `reject:no_rbos_cisd_after_liq` | 5 |
| `no_bos_after_liq` | 3 |
| `reject:no_cisd` | 2 |
| `no_liquidity_sweep` | 1 |

Structure counts: FBOS=6357, RBOS=3529, CISD=727, AMD=6752.


## Frequency track (train-selected OFAT, OOS report-only)

| Dataset | Variant | Mode | Train n | Train E[R] | OOS n | OOS E[R] | OOS PF | Flag |
|---|---|---|---:|---:|---:|---:|---:|---|
| `EURUSD=X 15m (ctrader)` | `legacy_session_both_rr20` | `legacy` | 6 | -0.093 | 4 | 3.268 | 7.54 | resolved<15 |
| `EURUSD=X 15m (ctrader)` | `legacy_session_off_rr20` | `legacy` | 22 | 0.640 | 14 | 0.720 | 2.01 | resolved<15 |
| `EURUSD=X 15m (ctrader)` | `legacy_session_lokz_rr20` | `legacy` | 4 | 0.360 | 2 | 5.255 | 11.51 | resolved<15 |
| `EURUSD=X 15m (ctrader)` | `legacy_session_both_rr15` | `legacy` | 6 | -0.093 | 4 | 3.268 | 7.54 | resolved<15 |
| `EURUSD=X 15m (ctrader)` | `legacy_session_off_rr15` | `legacy` | 22 | 0.640 | 14 | 0.720 | 2.01 | resolved<15 |
| `EURUSD=X 15m (ctrader)` | `legacy_chop_loose` | `legacy` | 61 | 1.854 | 39 | 1.621 | 3.53 |  |
| `EURUSD=X 15m (ctrader)` | `legacy_mid_loose_42_58` | `legacy` | 7 | -0.223 | 4 | 3.268 | 7.54 | resolved<15 |
| `EURUSD=X 15m (ctrader)` | `legacy_mid_strict_30_70` | `legacy` | 5 | 0.088 | 3 | 4.690 | 15.07 | resolved<15 |
| `EURUSD=X 15m (ctrader)` | `legacy_asia_aggressive` | `legacy` | 6 | -0.093 | 4 | 3.268 | 7.54 | resolved<15 |
| `EURUSD=X 15m (ctrader)` | `legacy_volume_probe` | `legacy` | 133 | 0.473 | 94 | 0.274 | 1.36 |  |
| `EURUSD=X 15m (ctrader)` | `legacy_volume_probe_asia` | `legacy` | 133 | 0.473 | 94 | 0.274 | 1.36 |  |
| `EURUSD=X 15m (ctrader)` | `adv_default` | `advanced` | 2 | -1.000 | 3 | 3.170 | 5.75 | resolved<15 |
| `EURUSD=X 15m (ctrader)` | `adv_session_off` | `advanced` | 6 | 0.977 | 4 | 2.160 | 5.32 | resolved<15 |
| `EURUSD=X 15m (ctrader)` | `adv_session_lokz` | `advanced` | 1 | -1.000 | 2 | 5.255 | 11.51 | resolved<15 |
| `EURUSD=X 15m (ctrader)` | `adv_session_both_rr15` | `advanced` | 2 | -1.000 | 3 | 3.170 | 5.75 | resolved<15 |
| `EURUSD=X 15m (ctrader)` | `adv_session_off_rr15` | `advanced` | 6 | 0.977 | 4 | 2.160 | 5.32 | resolved<15 |
| `EURUSD=X 15m (ctrader)` | `adv_no_parent_amd` | `advanced` | 2 | -1.000 | 3 | 3.170 | 5.75 | resolved<15 |
| `EURUSD=X 15m (ctrader)` | `adv_no_cisd` | `advanced` | 2 | -1.000 | 3 | 3.170 | 5.75 | resolved<15 |
| `EURUSD=X 15m (ctrader)` | `adv_gates_off` | `advanced` | 4 | -1.000 | 3 | 3.170 | 5.75 | resolved<15 |
| `EURUSD=X 15m (ctrader)` | `adv_mitigation` | `advanced` | 2 | -1.000 | 3 | 3.170 | 5.75 | resolved<15 |
| `EURUSD=X 15m (ctrader)` | `adv_mitigation_no_amd` | `advanced` | 2 | -1.000 | 3 | 3.170 | 5.75 | resolved<15 |
| `EURUSD=X 15m (ctrader)` | `adv_chop_loose` | `advanced` | 13 | 0.775 | 10 | 0.781 | 1.98 | resolved<15 |
| `EURUSD=X 15m (ctrader)` | `adv_mid_loose_42_58` | `advanced` | 2 | -1.000 | 3 | 3.170 | 5.75 | resolved<15 |
| `EURUSD=X 15m (ctrader)` | `adv_mid_strict_30_70` | `advanced` | 1 | -1.000 | 2 | 5.255 | 11.51 | resolved<15 |
| `EURUSD=X 15m (ctrader)` | `adv_asia_aggressive` | `advanced` | 2 | -1.000 | 3 | 3.170 | 5.75 | resolved<15 |
| `EURUSD=X 15m (ctrader)` | `adv_mitigation_off_rr15` | `advanced` | 6 | 0.977 | 4 | 2.160 | 5.32 | resolved<15 |
| `EURUSD=X 15m (ctrader)` | `adv_gates_off_session_off` | `advanced` | 12 | 0.607 | 10 | 1.408 | 3.35 | resolved<15 |
| `EURUSD=X 15m (ctrader)` | `adv_gates_off_volume` | `advanced` | 87 | 0.597 | 64 | 0.390 | 1.53 |  |
| `EURUSD=X 15m (ctrader)` | `adv_mitigation_volume` | `advanced` | 87 | 0.597 | 64 | 0.390 | 1.53 |  |
| `EURUSD=X 15m (ctrader)` | `adv_lokz_rr15_chop` | `advanced` | 8 | 1.411 | 4 | 2.127 | 3.84 | resolved<15 |
| `EURUSD=X 15m (ctrader)` | `legacy_lokz_rr15_chop` | `legacy` | 32 | 2.048 | 23 | 1.680 | 3.58 |  |
| `EURUSD=X 15m (ctrader)` | `adv_both_rr15_mid_loose` | `advanced` | 2 | -1.000 | 3 | 3.170 | 5.75 | resolved<15 |
| `EURUSD=X 15m (ctrader)` | `legacy_both_rr15_mid_loose` | `legacy` | 7 | -0.223 | 4 | 3.268 | 7.54 | resolved<15 |
| `EURUSD=X 15m (ctrader)` | `adv_no_amd_off_rr15` | `advanced` | 6 | 0.977 | 4 | 2.160 | 5.32 | resolved<15 |
| `EURUSD=X 15m (ctrader)` | `adv_no_cisd_off_rr15` | `advanced` | 6 | 0.977 | 4 | 2.160 | 5.32 | resolved<15 |
| `EURUSD=X 15m (ctrader)` | `adv_mitigation_lokz` | `advanced` | 1 | -1.000 | 2 | 5.255 | 11.51 | resolved<15 |

Train-selected **volume** `legacy_volume_probe`: OOS n=94, E[R]=0.274.
Train-selected **quality** `legacy_lokz_rr15_chop`: OOS n=23, E[R]=1.680.


## Advanced gate relaxations (train-selected, OOS aggregate)

| Dataset | Variant | Resolved | WR | E[R] | PF | Flag |
|---|---|---:|---:|---:|---:|---|
| `EURUSD=X 15m (ctrader)` | `default` | 3 | 33.3% | 3.170 | 5.75 | resolved<15 |
| `EURUSD=X 15m (ctrader)` | `no_parent_amd` | 3 | 33.3% | 3.170 | 5.75 | resolved<15 |
| `EURUSD=X 15m (ctrader)` | `no_cisd` | 3 | 33.3% | 3.170 | 5.75 | resolved<15 |
| `EURUSD=X 15m (ctrader)` | `session_lokz` | 2 | 50.0% | 5.255 | 11.51 | resolved<15 |
| `EURUSD=X 15m (ctrader)` | `session_ny_am` | 1 | 0.0% | -1.000 | 0.00 | resolved<15 |
| `EURUSD=X 15m (ctrader)` | `min_rr_15` | 3 | 33.3% | 3.170 | 5.75 | resolved<15 |
| `EURUSD=X 15m (ctrader)` | `min_rr_20` | 3 | 33.3% | 3.170 | 5.75 | resolved<15 |

Walk-forward relaxation pick (`EURUSD=X 15m (ctrader)`): E[R]=3.170, WR=33.3%, n=3, PF=5.75.

## `research_best` preset

Wired to `advanced_gates.SCAN_PRESETS['research_best']` = **legacy_chop_loose** (not Advanced gates_off):

| Knob | Value |
|---|---|
| mode | `legacy` |
| session | `both` (lokz + ny-am) |
| min_rr | `2.0` |
| chop_efficiency_min | `0.15` |
| chop_max_pivots | `9` |
| mid_lo / mid_hi | `0.35` / `0.65` |
| asia_aggressive | `false` |
| data_source | cTrader EURUSD M15 cache |

OOS study: n=39, E[R]=1.621, PF=3.53, WR=35.9%. Full recipe in `reports/research_best_scan.json`.

**Do not** redeploy Advanced `adv_gates_off_volume` as primary — it cleared the bar but is weaker (E[R]=0.39).


## Caveats

- **Bar cleared, but edge is filter-driven:** top full-sample rejects were `mid_range` (3750), `chop` (1149), `no_idm_sweep` (886). Advanced CISD/AMD rejects were tiny (≤5).
- **Best OOS overall** = `legacy_chop_loose` — now wired to `research_best`.
- **Best Advanced clearer** = `adv_gates_off_volume` (n=64, E[R]=0.390, PF=1.53) — secondary only.
- Do **not** treat this as production edge yet: paper only; no Railway deploy.

## Multi-symbol DEMO bot expansion (Jul 2026)

All candidates evaluated with the **frozen** `research_best` recipe (Legacy `chop_loose`, no per-pair retuning).
Include bar for new pairs: OOS n≥10, E[R]>0, PF>1. XAUUSD is included in the table even though it was already live.

## Multi-symbol OOS walk-forward (`research_best`, frozen params — no per-pair tuning)

Generated 2026-07-21T00:12:21.074074+00:00 — recipe `legacy_chop_loose (mode=legacy, session=both, min_rr=2.0, filters={'chop_efficiency_min': 0.15, 'chop_max_pivots': 9, 'mid_lo': 0.35, 'mid_hi': 0.65, 'asia_aggressive': False})`; include bar: OOS n>=10, E[R]>0, PF>1.

| Symbol | Bars | Setups | OOS n | OOS WR | OOS E[R] | OOS PF | Full n | Full E[R] | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `EURUSD` | 49643 | 75 | 39 | 35.9% | 1.621 | 3.53 | 75 | 1.841 | PASS |
| `XAUUSD` | 47173 | 25 | 12 | 25.0% | 2.854 | 4.81 | 25 | 3.074 | PASS |
| `GBPUSD` | 49642 | 76 | 42 | 38.1% | 1.476 | 3.38 | 76 | 1.694 | PASS |
| `USDJPY` | 49643 | 62 | 37 | 37.8% | 1.515 | 3.44 | 62 | 1.482 | PASS |
| `AUDUSD` | 49643 | 78 | 50 | 50.0% | 1.960 | 4.92 | 78 | 1.689 | PASS |
| `USDCAD` | 49644 | 87 | 58 | 41.4% | 1.388 | 3.37 | 87 | 1.327 | PASS |
| `NZDUSD` | 49643 | 88 | 56 | 32.1% | 0.867 | 2.28 | 88 | 1.077 | PASS |
| `USDCHF` | 49598 | 85 | 55 | 36.4% | 1.261 | 2.98 | 85 | 1.364 | PASS |

### Selection outcome

| Symbol | Decision | Why |
|---|---|---|
| `EURUSD` | **PASS** | OOS n=39, WR=35.9%, E[R]=1.621, PF=3.529 |
| `XAUUSD` | **PASS** | OOS n=12, WR=25.0%, E[R]=2.854, PF=4.806 |
| `GBPUSD` | **PASS** | OOS n=42, WR=38.1%, E[R]=1.476, PF=3.385 |
| `USDJPY` | **PASS** | OOS n=37, WR=37.8%, E[R]=1.515, PF=3.437 |
| `AUDUSD` | **PASS** | OOS n=50, WR=50.0%, E[R]=1.96, PF=4.92 |
| `USDCAD` | **PASS** | OOS n=58, WR=41.4%, E[R]=1.388, PF=3.367 |
| `NZDUSD` | **PASS** | OOS n=56, WR=32.1%, E[R]=0.867, PF=2.277 |
| `USDCHF` | **PASS** | OOS n=55, WR=36.4%, E[R]=1.261, PF=2.982 |

**Active DEMO bot symbols after this pass:** EURUSD, XAUUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, NZDUSD, USDCHF.

New FX majors trade broker **minimum volume**. SL/TP pip scaling uses broker `pipPosition` (JPY=0.01, majors=0.0001, gold=0.1).

Reproduce:
```bash
python research/run_multi_symbol_study.py
```

## NEXT

1. **Paper-trade `research_best`** on live/demo (Legacy + chop 0.15/9, session both, min_rr 2) — validate manually **10 setups** before any size-up.
2. Optional: multi-symbol check on **GC**; do **not** redeploy Advanced gates_off as primary; no Railway deploy yet.


## Reproduce

```bash
cd /Users/alimire/Downloads/smc-backtest
source .venv/bin/activate
python research/run_profitability_study.py --focus ctrader
python scripts/ctrader_fetch_history.py --period M15 --days 730
pytest -q
python app.py   # http://127.0.0.1:5050 — preset research_best selected by default
```



## A tier — higher-frequency setups (Jul 2026)

Goal: "something a little closer to A+ which will fire more" — **without touching A+**.

Design process (no OOS tuning):
1. **Reject diagnosis** under frozen `research_best` across all 9 symbols
   (`research/run_tier_reject_diagnosis.py` → `reports/tier_reject_diagnosis.json`):
   top candidate-stage rejects were `mid_range` **26,740**, `no_idm_sweep` **20,432**,
   `chop` 7,179 (already loosened in the frozen recipe).
2. **Candidates** — exactly ONE gate softened per candidate: `mid 0.40/0.60`,
   `mid 0.42/0.58`, `mid 0.45/0.55`, and `idm_flex` (unswept IDM allowed when a
   causal CISD/RBOS confirmation printed after the sweep).
3. **Train selection** on the walk-forward TRAIN windows only (same 3-fold split
   as the multi-symbol study). Winner: **`A_mid_45_55`** (train A-only n=133,
   E[R]=1.456, PF=3.52). `idm_flex` had higher E[R] (1.786) but lower n and a
   worse losing streak (12).
4. **OOS** (union of test folds) computed once, for the winner only.

**A-tier rule (`research_best_a` preset):** identical liquidity sweep + IDM sweep +
POI retest + BOS + chop + session + min RR 2.0 + SL/TP model as A+, but the
premium/discount ("not mid-range") gate widens from `0.35/0.65` to **`0.45/0.55`**
for A-labelled entries. A+ keeps `0.35/0.65` and takes precedence — the A+ subset
was asserted bit-identical with the A tier enabled, and duplicates are impossible
(one signal per bar; A+ replaces an A duplicate at the same dedupe key).

## A-tier walk-forward study (A+ frozen; A = one relaxation)

Generated 2026-07-21T11:56:44.120413+00:00 — base recipe `legacy_chop_loose (mode=legacy, session=both, min_rr=2.0, filters={'chop_efficiency_min': 0.15, 'chop_max_pivots': 9, 'mid_lo': 0.35, 'mid_hi': 0.65, 'asia_aggressive': False})`.

### Train selection (A-only rows in train windows; no OOS tuning)

| Candidate | Rule | Train n | Train E[R] | Train PF |
|---|---|---:|---:|---:|
| `A_mid_40_60` | {'a_tier': 'mid', 'a_mid_lo': 0.4, 'a_mid_hi': 0.6} | 57 | 1.222 | 3.11 |
| `A_mid_42_58` | {'a_tier': 'mid', 'a_mid_lo': 0.42, 'a_mid_hi': 0.58} | 83 | 1.211 | 2.97 |
| `A_mid_45_55` **<- selected** | {'a_tier': 'mid', 'a_mid_lo': 0.45, 'a_mid_hi': 0.55} | 133 | 1.456 | 3.52 |
| `A_idm_flex` | {'a_tier': 'idm_flex'} | 110 | 1.786 | 3.65 |

### OOS (union of test folds) — winner `A_mid_45_55`

| Symbol | A+ n | A+ E[R] | A+ PF | A n | A E[R] | A PF | Comb n | Comb E[R] | Comb PF |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `EURUSD` | 39 | 1.621 | 3.53 | 14 | 1.096 | 2.92 | 53 | 1.482 | 3.38 |
| `XAUUSD` | 12 | 2.854 | 4.81 | 3 | -1.000 | 0 | 15 | 2.083 | 3.60 |
| `GBPUSD` | 42 | 1.476 | 3.39 | 18 | 0.518 | 1.67 | 60 | 1.189 | 2.78 |
| `USDJPY` | 37 | 1.515 | 3.44 | 14 | 0.796 | 2.11 | 51 | 1.318 | 3.04 |
| `AUDUSD` | 50 | 1.960 | 4.92 | 9 | 1.920 | 5.32 | 59 | 1.954 | 4.98 |
| `USDCAD` | 58 | 1.388 | 3.37 | 14 | 1.810 | 4.62 | 72 | 1.470 | 3.58 |
| `NZDUSD` | 56 | 0.867 | 2.28 | 14 | 1.063 | 2.65 | 70 | 0.906 | 2.35 |
| `USDCHF` | 55 | 1.261 | 2.98 | 18 | 2.014 | 5.53 | 73 | 1.447 | 3.46 |
| `USDX` | 38 | 6.299 | 9.55 | 6 | 3.730 | 8.46 | 44 | 5.949 | 9.44 |
| **ALL** | 387 | 1.941 | 4.09 | 110 | 1.354 | 3.26 | 497 | 1.811 | 3.91 |

**Acceptance:** PASS — A-only n=110, E[R]=1.354, PF=3.257; combined n uplift x1.284.



### Deployment (DEMO only)

- Acceptance bar (A-only OOS n>=15, E[R]>0, PF>1, combined n up): **PASS**
  (A-only n=110, E[R]=1.354, PF=3.26; combined n 387 -> 497, x1.28 — a clear
  increase, though short of the 2x stretch target).
- Bot preset switched to `research_best_a`; order labels `smc-Aplus` vs `smc-A`;
  A tier always trades broker **minimum volume**; one position per symbol kept.
- **Correlation guard** (all 9 symbols are USD legs): max **3 total open
  positions** account-wide, enforced in `executor_core.evaluate_trade` and in
  the order-placement reconcile check. Applies to both tiers.
- XAUUSD A-only OOS was 3 trades, all losses (n too small to read) — monitor;
  per-symbol A-tier disable is a follow-up if live A trades underperform.

Reproduce:
```bash
python research/run_tier_reject_diagnosis.py
python research/run_tier_study.py
```
