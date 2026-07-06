"""Forward simulation: did price hit TP or SL first after each setup?"""

from __future__ import annotations

from datetime import datetime
from typing import Any

try:
    import yfinance as yf
except ImportError:
    yf = None


def parse_time(t_str: str) -> datetime:
    if "+" in t_str:
        t_str = t_str.split("+")[0].strip()
    elif t_str.count("-") > 2:
        t_str = t_str.rsplit("-", 1)[0].strip()
    if isinstance(t_str, datetime):
        return t_str.replace(tzinfo=None) if t_str.tzinfo else t_str
    return datetime.strptime(str(t_str)[:19], "%Y-%m-%d %H:%M:%S")


def _find_candle_index(candles: list[dict], setup_time: datetime) -> int:
    for idx, c in enumerate(candles):
        t = c["time"]
        if hasattr(t, "tzinfo") and t.tzinfo:
            t = t.replace(tzinfo=None)
        if t == setup_time:
            return idx
    best_idx = -1
    best_diff = None
    for idx, c in enumerate(candles):
        t = c["time"]
        if hasattr(t, "replace") and t.tzinfo:
            t = t.replace(tzinfo=None)
        diff = abs((t - setup_time).total_seconds())
        if best_diff is None or diff < best_diff:
            best_diff = diff
            best_idx = idx
    if best_diff is not None and best_diff > 15 * 60:
        return -1
    return best_idx


def _simulate_one(candles: list[dict], start_idx: int, setup: dict) -> tuple[str, str | None]:
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


def fetch_candles(symbol: str, days: int, interval: str) -> list[dict]:
    if yf is None:
        raise ImportError("yfinance required for simulation")
    df = yf.Ticker(symbol).history(period=f"{days}d", interval=interval)
    if df.empty:
        raise ValueError(f"No candle data for {symbol}")
    candles = []
    for ts, row in df.iterrows():
        candles.append({
            "time": ts.to_pydatetime(),
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
        })
    return candles


def simulate_rows(
    rows: list[dict],
    symbol: str,
    days: int,
    interval: str,
    aplus_only: bool = True,
) -> dict[str, Any]:
    """
    Run forward simulation on setup rows (dicts with entry/SL/TP/direction/time).
    Returns summary stats + rows with outcome / exit_time added.
    """
    targets = [r for r in rows if (not aplus_only or r.get("is_aplus"))]
    if not targets:
        return {
            "sim_wins": 0,
            "sim_losses": 0,
            "sim_unresolved": 0,
            "sim_win_rate": 0.0,
            "avg_planned_rr": 0.0,
            "expectancy_r": 0.0,
            "rows": rows,
        }

    candles = fetch_candles(symbol, days, interval)
    wins = losses = unresolved = 0
    planned_rr: list[float] = []

    for s in targets:
        planned_rr.append(float(s.get("rr_ratio") or 0))
        idx = _find_candle_index(candles, parse_time(s["time"]))
        if idx < 0:
            outcome, exit_time = "Unresolved", None
        else:
            outcome, exit_time = _simulate_one(candles, idx, s)

        s["outcome"] = outcome
        s["exit_time"] = exit_time or "N/A"

        if outcome == "Win":
            wins += 1
        elif outcome == "Loss":
            losses += 1
        else:
            unresolved += 1

    for r in rows:
        if not r.get("is_aplus"):
            r["outcome"] = "—"
            r["exit_time"] = "—"

    resolved = wins + losses
    win_rate = (wins / resolved * 100) if resolved else 0.0
    avg_rr = sum(planned_rr) / len(planned_rr) if planned_rr else 0.0
    win_rrs = [float(s.get("rr_ratio") or 0) for s in targets if s.get("outcome") == "Win"]
    avg_win_rr = sum(win_rrs) / len(win_rrs) if win_rrs else avg_rr
    loss_rate = losses / resolved if resolved else 0
    win_frac = wins / resolved if resolved else 0
    expectancy = (win_frac * avg_win_rr) - (loss_rate * 1.0) if resolved else 0.0

    return {
        "sim_wins": wins,
        "sim_losses": losses,
        "sim_unresolved": unresolved,
        "sim_win_rate": round(win_rate, 1),
        "avg_planned_rr": round(avg_rr, 2),
        "expectancy_r": round(expectancy, 2),
        "rows": rows,
    }
