"""Forward simulation: did price hit TP or SL first after each setup?"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

try:
    import yfinance as yf
except ImportError:
    yf = None


def parse_time(t_str) -> datetime:
    if isinstance(t_str, datetime):
        return t_str.replace(tzinfo=None) if t_str.tzinfo else t_str
    t_str = str(t_str)
    if "+" in t_str:
        t_str = t_str.split("+")[0].strip()
    elif t_str.count("-") > 2:
        t_str = t_str.rsplit("-", 1)[0].strip()
    return datetime.strptime(str(t_str)[:19], "%Y-%m-%d %H:%M:%S")


def _find_candle_index(candles: list[dict], setup_time: datetime, max_diff_sec: float = 15 * 60) -> int:
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
        if hasattr(t, "replace") and getattr(t, "tzinfo", None):
            t = t.replace(tzinfo=None)
        diff = abs((t - setup_time).total_seconds())
        if best_diff is None or diff < best_diff:
            best_diff = diff
            best_idx = idx
    if best_diff is not None and best_diff > max_diff_sec:
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
    from data_fetch import fetch_ohlcv, df_to_candle_dicts

    df, _ = fetch_ohlcv(symbol, days, interval, use_cache=True)
    return df_to_candle_dicts(df)


def candles_from_objects(candles) -> list[dict]:
    """Accept list[Candle] or list[dict]."""
    out = []
    for c in candles:
        if isinstance(c, dict):
            out.append(c)
        else:
            out.append({
                "time": c.time,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
            })
    return out


def compute_metrics(targets: list[dict]) -> dict[str, Any]:
    """Expectancy / PF / streak from rows that already have outcome set."""
    wins = losses = unresolved = 0
    planned_rr: list[float] = []
    r_series: list[float] = []
    streak = max_lose = cur_lose = 0

    for s in targets:
        planned_rr.append(float(s.get("rr_ratio") or 0))
        outcome = s.get("outcome")
        if outcome == "Win":
            wins += 1
            rr = float(s.get("rr_ratio") or 0)
            r_series.append(rr)
            cur_lose = 0
        elif outcome == "Loss":
            losses += 1
            r_series.append(-1.0)
            cur_lose += 1
            max_lose = max(max_lose, cur_lose)
        else:
            unresolved += 1

    resolved = wins + losses
    win_rate = (wins / resolved * 100) if resolved else 0.0
    avg_rr = sum(planned_rr) / len(planned_rr) if planned_rr else 0.0
    win_rrs = [float(s.get("rr_ratio") or 0) for s in targets if s.get("outcome") == "Win"]
    avg_win_rr = sum(win_rrs) / len(win_rrs) if win_rrs else avg_rr
    loss_rate = losses / resolved if resolved else 0
    win_frac = wins / resolved if resolved else 0
    expectancy = (win_frac * avg_win_rr) - (loss_rate * 1.0) if resolved else 0.0
    total_r = sum(r_series)
    gross_win = sum(r for r in r_series if r > 0)
    gross_loss = abs(sum(r for r in r_series if r < 0))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)

    return {
        "setups": len(targets),
        "sim_wins": wins,
        "sim_losses": losses,
        "sim_unresolved": unresolved,
        "resolved": resolved,
        "sim_win_rate": round(win_rate, 1),
        "avg_planned_rr": round(avg_rr, 2),
        "avg_win_rr": round(avg_win_rr, 2),
        "expectancy_r": round(expectancy, 3),
        "profit_factor": round(profit_factor, 3) if profit_factor != float("inf") else None,
        "profit_factor_raw": profit_factor,
        "total_r": round(total_r, 2),
        "max_losing_streak": max_lose,
    }


def simulate_rows(
    rows: list[dict],
    symbol: str = "EURUSD=X",
    days: int = 60,
    interval: str = "15m",
    aplus_only: bool = True,
    candles: Optional[list] = None,
) -> dict[str, Any]:
    """
    Run forward simulation on setup rows (dicts with entry/SL/TP/direction/time).
    Returns summary stats + rows with outcome / exit_time added.
    """
    targets = [r for r in rows if (not aplus_only or r.get("is_aplus"))]
    empty = {
        "setups": 0,
        "sim_wins": 0,
        "sim_losses": 0,
        "sim_unresolved": 0,
        "resolved": 0,
        "sim_win_rate": 0.0,
        "avg_planned_rr": 0.0,
        "avg_win_rr": 0.0,
        "expectancy_r": 0.0,
        "profit_factor": 0.0,
        "profit_factor_raw": 0.0,
        "total_r": 0.0,
        "max_losing_streak": 0,
        "rows": rows,
    }
    if not targets:
        return empty

    if candles is None:
        candle_dicts = fetch_candles(symbol, days, interval)
    else:
        candle_dicts = candles_from_objects(candles)

    # Tolerate up to one bar of the active interval for index matching
    from data_fetch import interval_minutes
    max_diff = interval_minutes(interval) * 60 * 1.5

    for s in targets:
        idx = _find_candle_index(candle_dicts, parse_time(s["time"]), max_diff_sec=max_diff)
        if idx < 0:
            outcome, exit_time = "Unresolved", None
        else:
            outcome, exit_time = _simulate_one(candle_dicts, idx, s)

        s["outcome"] = outcome
        s["exit_time"] = exit_time or "N/A"

    for r in rows:
        if not r.get("is_aplus"):
            r["outcome"] = "—"
            r["exit_time"] = "—"

    metrics = compute_metrics(targets)
    metrics["rows"] = rows
    return metrics
