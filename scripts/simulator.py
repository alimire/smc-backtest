#!/usr/bin/env python3
"""Simulate A+ setups forward: did price hit TP or SL first?"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent


def parse_time(t_str: str) -> datetime:
    if "+" in t_str:
        t_str = t_str.split("+")[0].strip()
    elif t_str.count("-") > 2:
        t_str = t_str.rsplit("-", 1)[0].strip()
    return datetime.strptime(t_str, "%Y-%m-%d %H:%M:%S")


def find_candle_index(candles: list[dict], setup_time: datetime) -> int:
    for idx, c in enumerate(candles):
        if c["time"].replace(tzinfo=None) == setup_time:
            return idx
    best_idx = 0
    best_diff = None
    for idx, c in enumerate(candles):
        diff = abs((c["time"].replace(tzinfo=None) - setup_time).total_seconds())
        if best_diff is None or diff < best_diff:
            best_diff = diff
            best_idx = idx
    if best_diff is not None and best_diff > 15 * 60:
        return -1
    return best_idx


def simulate_setup(candles: list[dict], start_idx: int, setup: dict) -> tuple[str, str | None]:
    sl = setup["stop_loss"]
    tp = setup["take_profit"]
    direction = setup["direction"]

    for c in candles[start_idx + 1 :]:
        if direction == "long":
            if c["low"] <= sl and c["high"] >= tp:
                return "Loss", str(c["time"])
            if c["low"] <= sl:
                return "Loss", str(c["time"])
            if c["high"] >= tp:
                return "Win", str(c["time"])
        else:
            if c["high"] >= sl and c["low"] <= tp:
                return "Loss", str(c["time"])
            if c["high"] >= sl:
                return "Loss", str(c["time"])
            if c["low"] <= tp:
                return "Win", str(c["time"])
    return "Unresolved", None


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

    print(f"Fetching {args.symbol} ({args.days}d @ {args.interval}) for simulation...")
    df = yf.Ticker(args.symbol).history(period=f"{args.days}d", interval=args.interval)
    if df.empty:
        print("Error: no candle data from yfinance")
        sys.exit(1)

    candles = []
    for ts, row in df.iterrows():
        candles.append({
            "time": ts.to_pydatetime(),
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
        })

    results = []
    wins = losses = unresolved = 0

    for s in aplus:
        idx = find_candle_index(candles, parse_time(s["time"]))
        if idx < 0:
            print(f"Warning: no candle match for {s['time']}")
            s["outcome"] = "Unresolved"
            s["exit_time"] = "N/A"
            unresolved += 1
            results.append(s)
            continue

        outcome, exit_time = simulate_setup(candles, idx, s)
        s["outcome"] = outcome
        s["exit_time"] = exit_time or "N/A"
        if outcome == "Win":
            wins += 1
        elif outcome == "Loss":
            losses += 1
        else:
            unresolved += 1
        results.append(s)

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(results, f, indent=2)

    resolved = wins + losses
    win_rate = (wins / resolved * 100) if resolved else 0.0
    print("\n=== SIMULATION RESULTS ===")
    print(f"Total A+ setups: {len(results)}")
    print(f"Wins: {wins}  Losses: {losses}  Unresolved: {unresolved}")
    print(f"Win rate: {win_rate:.2f}%")
    print(f"Saved → {args.output}")


if __name__ == "__main__":
    main()
