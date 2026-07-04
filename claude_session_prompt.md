# Ruflo / Claude Session Prompt — SMC Backtest Pipeline

Use after running `./scripts/run_pipeline.sh` (or the three Python steps below).

---

## Setup (once per machine)

```bash
cd smc-backtest
npx ruflo@latest init --wizard
claude mcp add ruflo -- npx -y ruflo@latest mcp start
```

---

## Regenerate data (before each review session)

```bash
./scripts/run_pipeline.sh 60 both
```

Or manually:

```bash
python3 smc_detector.py --days 60 --session both --report json --output smc_report
python3 scripts/simulator.py
python3 scripts/generate_review.py
```

---

## Paste this into Ruflo / Claude Code

I am reviewing an SMC backtest on **EURUSD** (15m, 60 days) calibrated to **Salim's rules**.

**Read first:**
- `SMC_DEFINITIONS.md` — IDM, POI, SMT, sessions (lokz / ny-am), A+ criteria, protected SL / liquidity TP
- `reports/expert_review.md` — full A+ trade log with Win/Loss simulation
- `simulated_report.json` — raw rows with `outcome`, `entry_price`, `stop_loss`, `take_profit`

**Your tasks:**

1. Confirm each A+ row matches Salim's sequence: liquidity sweep → IDM sweep → POI retest in kill zone.
2. Flag setups where IDM was swept **on the entry candle** (may be too aggressive).
3. Spot-check these benchmark trades Salim mentioned:
   - **3 July ~9am UK** — EURUSD short near 1.1457 (lokz)
   - **27 May ~1.165** — EURUSD short (ny-am)
4. Summarize for Salim:
   - Total A+, win rate, SMT count
   - Table of questionable setups only (wrong session, weak IDM, RR &lt; 2)
   - Suggested rule tweaks for `smc_detector.py`

**Output:** Update `reports/expert_review.md` or a short `reports/salim_feedback.md` Salim can reply to on WhatsApp.

---
