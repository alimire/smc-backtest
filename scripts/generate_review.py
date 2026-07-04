#!/usr/bin/env python3
"""Build expert_review.md for Salim from simulated_report.json."""

import argparse
import json
from datetime import datetime
from pathlib import Path

import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent


def parse_time(t_str: str) -> datetime:
    return datetime.strptime(t_str.split("+")[0].strip(), "%Y-%m-%d %H:%M:%S")


def find_swing_points(candles: list[dict], lookback: int = 3) -> list[dict]:
    swings = []
    for i in range(lookback, len(candles) - lookback):
        wh = [c["high"] for c in candles[i - lookback : i + lookback + 1]]
        wl = [c["low"] for c in candles[i - lookback : i + lookback + 1]]
        if candles[i]["high"] == max(wh):
            swings.append({"index": i, "price": candles[i]["high"], "kind": "high"})
        if candles[i]["low"] == min(wl):
            swings.append({"index": i, "price": candles[i]["low"], "kind": "low"})
    return swings


def idm_timing(candles: list[dict], swings: list[dict], idx: int, direction: str) -> tuple[str, bool]:
    kind = "high" if direction == "short" else "low"
    from_idx = idx - 48
    candidates = [sw for sw in swings if sw["kind"] == kind and from_idx <= sw["index"] < idx]
    is_synthetic = False

    if candidates:
        idm = candidates[-1]
    else:
        start = max(from_idx, idx - 12)
        window = candles[start:idx]
        if not window:
            return "Unknown", False
        if direction == "short":
            best = max(range(len(window)), key=lambda k: window[k]["high"])
            ci = start + best
            idm = {"index": ci, "price": window[best]["high"]}
        else:
            best = min(range(len(window)), key=lambda k: window[k]["low"])
            ci = start + best
            idm = {"index": ci, "price": window[best]["low"]}
        is_synthetic = True

    buf = 0.00015
    for k in range(idm["index"] + 1, idx):
        c = candles[k]
        if direction == "short" and c["high"] >= idm["price"] - buf:
            return "Before Entry ✓", is_synthetic
        if direction == "long" and c["low"] <= idm["price"] + buf:
            return "Before Entry ✓", is_synthetic
    return "On Entry Candle ⚠️", is_synthetic


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Salim expert review markdown")
    parser.add_argument("--input", default=str(ROOT / "simulated_report.json"))
    parser.add_argument("--output", default=str(ROOT / "reports" / "expert_review.md"))
    parser.add_argument("--symbol", default="EURUSD=X")
    parser.add_argument("--days", type=int, default=60)
    args = parser.parse_args()

    with open(args.input) as f:
        setups = json.load(f)

    df = yf.Ticker(args.symbol).history(period=f"{args.days}d", interval="15m")
    candles = [
        {
            "time": ts.to_pydatetime(),
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
        }
        for ts, row in df.iterrows()
    ]
    swings = find_swing_points(candles)

    rows = []
    wins = losses = unresolved = before_count = coincident_count = 0

    for s in setups:
        s_time = parse_time(s["time"])
        idx = next(
            (i for i, c in enumerate(candles) if c["time"].replace(tzinfo=None) == s_time),
            -1,
        )
        timing, synthetic = ("Unknown", False) if idx < 0 else idm_timing(candles, swings, idx, s["direction"])
        if timing == "Before Entry ✓":
            before_count += 1
        elif timing.startswith("On Entry"):
            coincident_count += 1

        outcome = s.get("outcome", "Unresolved")
        if outcome == "Win":
            wins += 1
            outcome_md = "**Win**"
        elif outcome == "Loss":
            losses += 1
            outcome_md = "Loss"
        else:
            unresolved += 1
            outcome_md = "Unresolved"

        note = s.get("notes", "")
        if synthetic:
            note = f"{note} (Synthetic IDM)".strip()

        rows.append(
            f"| {s['time'][:16]} | {s['direction'].upper()} | {s['session']} | {s['poi_type']} | "
            f"{'✓' if s['smt_confluence'] else '✗'} | {s['rr_ratio']} | {outcome_md} | {timing} | {note} |"
        )

    n = len(setups) or 1
    win_rate = (wins / (wins + losses) * 100) if (wins + losses) else 0.0

    md = f"""# SMC Expert Review — Salim's Rules

EURUSD 15m · last {args.days} days · generated from `simulated_report.json`

## Executive Summary

| Metric | Value |
| :--- | :--- |
| **Total A+ setups** | {len(setups)} |
| **Wins (TP first)** | {wins} |
| **Losses (SL first)** | {losses} |
| **Unresolved** | {unresolved} |
| **Win rate** | **{win_rate:.2f}%** |

## IDM timing

- **Before entry:** {before_count} ({before_count / n * 100:.1f}%)
- **On entry candle:** {coincident_count} ({coincident_count / n * 100:.1f}%) — Salim should verify these

## Trade log

| Time | Dir | Session | POI | SMT | RR | Outcome | IDM timing | Notes |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
"""
    md += "\n".join(rows) + "\n"

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
