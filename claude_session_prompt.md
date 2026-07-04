# Ruflo / Claude Session Init Prompt

Paste this as your first message when starting a Claude or Ruflo session:

---

I am running an SMC (Smart Money Concepts) backtest on EURUSD for the last 60 days.

**Context file:** Please read `SMC_DEFINITIONS.md` in this folder for the exact definitions
of IDM, POI (OB/FVG), SMT, BOS, PDH/PDL, and A+ setup criteria.

**Pre-computed data:** The file `smc_report.json` (or `smc_report.html`) contains the output
from my detection script. Each row represents a potential trade setup.

**Your tasks:**

1. Read `SMC_DEFINITIONS.md` first so you understand the exact terminology.
2. Load `smc_report.json` and filter for rows where `is_aplus: true`.
3. For each A+ setup, verify:
   - Was the PDH or PDL actually swept (wick beyond, then close back inside)?
   - Was the IDM swept before price reached the POI?
   - Is the POI type (OB / FVG / OB+FVG) consistent with the definition in SMC_DEFINITIONS.md?
   - If SMT confluence is marked true, note the DXY divergence detail.
4. Generate a clean expert-review summary with:
   - Total A+ setups
   - Win rate (based on whether price reached TP before SL)
   - A table: Time | Direction | Session | POI Type | SMT | Notes
   - Flag any setup where the IDM-to-POI sequence seems questionable

**Expert review note:** This output will be reviewed by an SMC expert (Salim) who will
verify that the IDM identification and POI reactions are accurate per the definitions.
Please make the reasoning transparent so he can spot any misclassifications.

---
