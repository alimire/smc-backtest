#!/usr/bin/env python3
"""Simulate A+ setups forward: did price hit TP or SL first?"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from simulator_core import simulate_rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulate SMC A+ trade outcomes")
    parser.add_argument("--input", default=str(ROOT / "smc_report.json"))
    parser.add_argument("--output", default=str(ROOT / "simulated_report.json"))
    parser.add_argument("--symbol", default="EURUSD=X")
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--interval", default="15m")
    args = parser.parse_args()

    with open(args.input) as f:
        setups = json.load(f)

    aplus = [s for s in setups if s.get("is_aplus")]
    if not aplus:
        print("No A+ setups in input report.")
        sys.exit(1)

    print(f"Simulating {len(aplus)} A+ setups on {args.symbol}...")
    result = simulate_rows(setups, args.symbol, args.days, args.interval)

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(result["rows"], f, indent=2)

    print("\n=== SIMULATION RESULTS ===")
    print(f"Total A+ setups: {len(aplus)}")
    print(f"Wins: {result['sim_wins']}  Losses: {result['sim_losses']}  "
          f"Unresolved: {result['sim_unresolved']}")
    print(f"Win rate (simulated): {result['sim_win_rate']}%")
    print(f"Avg planned RR: {result['avg_planned_rr']}  Expectancy: {result['expectancy_r']}R")
    print(f"Saved → {args.output}")


if __name__ == "__main__":
    main()
