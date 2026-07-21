# SMC Backtest (Salim / Kaanq research engine)

Python research engine for Salim v3 / Kaanq Advanced SMC logic. ATAS C# parity lives in `../atas-smc-strategy`.

## Quick start

```bash
cd /Users/alimire/Downloads/smc-backtest
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python research/run_profitability_study.py
pytest -q
python smc_detector.py --symbol EURUSD=X --interval 1h --days 730 --strategy-mode advanced
```

## Research outputs

- `docs/RESEARCH_RESULTS.md` — honest summary table + reproduction commands
- `reports/profitability_study.json` — full walk-forward JSON
- `data_cache/` — cached Yahoo OHLC (never fabricated)

## Data notes (Jul 2026)

| Source | Interval | Span | Notes |
|---|---|---:|---|
| **cTrader demo** | M15 | ~24mo / ~49.6k bars | **Primary research surface** — `fetch_broker_15m("ctrader")` / `data_cache/EURUSD_ctrader_M15_730d.csv` |
| Yahoo | 15m / 5m | ~60d | Cap still applies; use only when broker cache missing |
| Yahoo | 1h | ~730d | Secondary cross-check |
| Yahoo | 1d | multi-year | HTF context only |
| Resampled | 4h | from 1h | Honest aggregation, not pseudo-15m |

Refresh broker history: `python scripts/ctrader_fetch_history.py --period M15 --days 730`.

Symbols verified locally: `EURUSD=X`, `GC=F`, `6E=F`, `M6E=F`, `DX-Y.NYB`. `XAUUSD=X` is **not** available on Yahoo here; use `GC=F` as gold proxy.

## Strategy modes

- `legacy` — mechanical v1.1.0 A+ scanner
- `advanced` — FBOS/RBOS + CISD + Parent AMD (v1.2.0 default)

Named presets:
- Advanced gate slots: `advanced_default`, `advanced_oos_candidate`
- Full-scan recipe: `research_best` = **Legacy chop_loose** on cTrader M15 (`SCAN_PRESETS` in `advanced_gates.py`)

```bash
python app.py   # http://127.0.0.1:5050 — research_best selected by default
```
