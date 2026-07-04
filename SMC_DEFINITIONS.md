# SMC Trading Methodology — Salim's Rules (coded)

## Sessions (UK time)

| Label | Window | Use |
|-------|--------|-----|
| **Asia** | 00:00–06:59 | Build Asia high/low liquidity |
| **lokz** | 07:00–09:59 | London kill zone (includes 9am UK) |
| **ny-am** | 14:30–16:00 | NY morning kill zone |
| **ny-pm** | 16:00–19:00 | NY afternoon |

Default scanner filter: **lokz + ny-am** (`session=both`).

## A+ sequence (all required)

1. **Liquidity sweep** — wick through PDH, PDL, Asia high/low, or recent swing extreme
2. **IDM sweep** — minor inducement swing swept before POI retest
3. **POI retest** — price returns to OB or FVG in a kill zone
4. **SMT** (optional confluence) — EURUSD vs DXY divergence at entry

## Entry model

- Scan for **POI retests** in kill zones (not BOS candle only)

## Risk parameters (Salim v2 — coded)

- **SL:** Beyond the **protected high/low** from the IDM sweep window (extreme between IDM and entry), plus buffer
- **TP:** Nearest **liquidity pool only** (PDH/PDL, Asia H/L, local swing) — no forced 2R override on TP
- **Filter:** Discard setup if liquidity-based RR is below 2.0

## Pipeline

```bash
bash scripts/run_pipeline.sh 60 both
```

Produces `smc_report.json` → `simulated_report.json` → `reports/expert_review.md`

## Symbols

- **EURUSD** — primary; DXY used for SMT
- **DXY** — scannable; EURUSD used for SMT
