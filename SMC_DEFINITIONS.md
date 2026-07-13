# SMC Trading Methodology — Salim's Rules (coded v3)

## Sessions (UK time)

| Label | Window | Use |
|-------|--------|-----|
| **Asia** | 00:00–06:59 | Build Asia high/low liquidity |
| **lokz** | 07:00–09:59 | London kill zone (includes 9am UK) |
| **ny-am** | 14:30–16:00 | NY morning kill zone |
| **ny-pm** | 16:00–19:00 | NY afternoon |

Default scanner filter: **lokz + ny-am** (`session=both`).

## A+ sequence (all required — v3)

1. **Liquidity sweep** — wick through PDH, PDL, Asia high/low, or recent swing extreme
2. **Real break (BOS)** — orderflow shift in trade direction **after** the liq sweep
3. **IDM sweep** — minor inducement swing swept before POI retest
4. **Major IDM zone** — POI/IDM in **premium** (shorts) or **discount** (longs), not mid-range (~35–65%)
5. **Not choppy** — skip tight alternating price action
6. **SMT tap rule** — if SMT divergence is nearby, **wait** until correlated pair (DXY) sweeps the SMT level before entry
7. **POI retest** — price returns to OB or FVG in a kill zone

## Entry model

- Scan for **POI retests** in kill zones (not BOS candle only)
- Only enter when **protected** after liq/IDM sweep + confirmed break

## Risk parameters (Salim v3)

- **SL:** Beyond the **protected high/low** from the IDM sweep window, plus buffer
- **TP:** Nearest **liquidity pool only** (PDH/PDL, Asia H/L, local swing) — no forced 2R TP
- **Filter:** Discard setup if liquidity-based RR is below 2.0

## TradingView

- Script: `tradingview/salim_smc_alerts.pine` (**Salim SMC A+ Alerts v3**)
- Create alerts in **desktop browser**: condition → **Any alert() function call** or **Short/Long A+ lokz**
- Trigger: **Once per bar close** · Replay **OFF**

## Pipeline

```bash
bash scripts/run_pipeline.sh 60 both
```

## Symbols

- **EURUSD** — primary; DXY used for SMT
- **DXY** — scannable; EURUSD used for SMT
