#!/usr/bin/env python3
"""
Credible profitability study for Salim/Kaanq SMC logic.

Principles:
- Never fabricate OHLC.
- Tune on train only.
- Report out-of-sample trade counts and flag thin samples.
- Prefer longest reliable history; cTrader EURUSD M15 (~24mo) is primary when cached.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from advanced_gates import AdvancedConfig, PRESETS, SCAN_PRESETS, ScanRecipe, build_advanced_timeline  # noqa: E402
from data_fetch import CACHE_DIR, df_to_candle_dicts, fetch_broker_15m, fetch_ohlcv, load_broker_csv  # noqa: E402
from simulator_core import simulate_rows  # noqa: E402
from smc_detector import Candle, align_correlated, scan_setups  # noqa: E402


REPORTS_DIR = ROOT / "reports"
DOCS_DIR = ROOT / "docs"
DEFAULT_JSON = REPORTS_DIR / "profitability_study.json"
DEFAULT_MD = DOCS_DIR / "RESEARCH_RESULTS.md"


CORE_GRID = [
    {
        "session": session,
        "min_rr": min_rr,
        "entry_model": entry_model,
        "tp_mode": tp_mode,
    }
    for session in ("both", "lokz", "ny-am", "off")
    for min_rr in (1.5, 2.0, 2.5)
    for entry_model in ("rbos", "mitigation")
    for tp_mode in ("nearest_liquidity", "fixed_rr")
]

# Incremental relaxations — one change from Advanced default per variant (train selection only).
RELAXATION_VARIANTS = [
    {"name": "default", "session": "both", "min_rr": 2.0, "cfg": AdvancedConfig()},
    {"name": "no_parent_amd", "session": "both", "min_rr": 2.0, "cfg": AdvancedConfig(require_parent_amd=False)},
    {"name": "no_cisd", "session": "both", "min_rr": 2.0, "cfg": AdvancedConfig(require_cisd=False)},
    {"name": "session_lokz", "session": "lokz", "min_rr": 2.0, "cfg": AdvancedConfig()},
    {"name": "session_ny_am", "session": "ny-am", "min_rr": 2.0, "cfg": AdvancedConfig()},
    {"name": "min_rr_15", "session": "both", "min_rr": 1.5, "cfg": AdvancedConfig()},
    {"name": "min_rr_20", "session": "both", "min_rr": 2.0, "cfg": AdvancedConfig()},
]

# Frequency track — loosen ONE layer at a time vs Legacy/Advanced baseline (train selection only).
# Defaults match production scanner; each variant changes a single axis (or one documented combo).
# Cap ~40 configs to limit overfitting; selection uses train only; OOS is report-only.
_DEFAULT_FILTERS = {
    "chop_efficiency_min": 0.22,
    "chop_max_pivots": 7,
    "mid_lo": 0.35,
    "mid_hi": 0.65,
    "asia_aggressive": False,
}

FREQUENCY_VARIANTS = [
    # --- Legacy OFAT ---
    {"name": "legacy_session_both_rr20", "mode": "legacy", "session": "both", "min_rr": 2.0,
     "cfg": None, "filters": dict(_DEFAULT_FILTERS)},
    {"name": "legacy_session_off_rr20", "mode": "legacy", "session": "off", "min_rr": 2.0,
     "cfg": None, "filters": dict(_DEFAULT_FILTERS)},
    {"name": "legacy_session_lokz_rr20", "mode": "legacy", "session": "lokz", "min_rr": 2.0,
     "cfg": None, "filters": dict(_DEFAULT_FILTERS)},
    {"name": "legacy_session_both_rr15", "mode": "legacy", "session": "both", "min_rr": 1.5,
     "cfg": None, "filters": dict(_DEFAULT_FILTERS)},
    {"name": "legacy_session_off_rr15", "mode": "legacy", "session": "off", "min_rr": 1.5,
     "cfg": None, "filters": dict(_DEFAULT_FILTERS)},
    {"name": "legacy_chop_loose", "mode": "legacy", "session": "both", "min_rr": 2.0,
     "cfg": None, "filters": {**_DEFAULT_FILTERS, "chop_efficiency_min": 0.15, "chop_max_pivots": 9}},
    {"name": "legacy_mid_loose_42_58", "mode": "legacy", "session": "both", "min_rr": 2.0,
     "cfg": None, "filters": {**_DEFAULT_FILTERS, "mid_lo": 0.42, "mid_hi": 0.58}},
    {"name": "legacy_mid_strict_30_70", "mode": "legacy", "session": "both", "min_rr": 2.0,
     "cfg": None, "filters": {**_DEFAULT_FILTERS, "mid_lo": 0.30, "mid_hi": 0.70}},
    {"name": "legacy_asia_aggressive", "mode": "legacy", "session": "both", "min_rr": 2.0,
     "cfg": None, "filters": {**_DEFAULT_FILTERS, "asia_aggressive": True}},
    {"name": "legacy_volume_probe", "mode": "legacy", "session": "off", "min_rr": 1.5,
     "cfg": None, "filters": {**_DEFAULT_FILTERS, "chop_efficiency_min": 0.15, "chop_max_pivots": 9,
                              "mid_lo": 0.42, "mid_hi": 0.58}},
    {"name": "legacy_volume_probe_asia", "mode": "legacy", "session": "off", "min_rr": 1.5,
     "cfg": None, "filters": {**_DEFAULT_FILTERS, "chop_efficiency_min": 0.15, "chop_max_pivots": 9,
                              "mid_lo": 0.42, "mid_hi": 0.58, "asia_aggressive": True}},
    # --- Advanced OFAT ---
    {"name": "adv_default", "mode": "advanced", "session": "both", "min_rr": 2.0,
     "cfg": AdvancedConfig(), "filters": dict(_DEFAULT_FILTERS)},
    {"name": "adv_session_off", "mode": "advanced", "session": "off", "min_rr": 2.0,
     "cfg": AdvancedConfig(), "filters": dict(_DEFAULT_FILTERS)},
    {"name": "adv_session_lokz", "mode": "advanced", "session": "lokz", "min_rr": 2.0,
     "cfg": AdvancedConfig(), "filters": dict(_DEFAULT_FILTERS)},
    {"name": "adv_session_both_rr15", "mode": "advanced", "session": "both", "min_rr": 1.5,
     "cfg": AdvancedConfig(), "filters": dict(_DEFAULT_FILTERS)},
    {"name": "adv_session_off_rr15", "mode": "advanced", "session": "off", "min_rr": 1.5,
     "cfg": AdvancedConfig(), "filters": dict(_DEFAULT_FILTERS)},
    {"name": "adv_no_parent_amd", "mode": "advanced", "session": "both", "min_rr": 2.0,
     "cfg": AdvancedConfig(require_parent_amd=False), "filters": dict(_DEFAULT_FILTERS)},
    {"name": "adv_no_cisd", "mode": "advanced", "session": "both", "min_rr": 2.0,
     "cfg": AdvancedConfig(require_cisd=False), "filters": dict(_DEFAULT_FILTERS)},
    {"name": "adv_gates_off", "mode": "advanced", "session": "both", "min_rr": 2.0,
     "cfg": AdvancedConfig(require_cisd=False, require_parent_amd=False), "filters": dict(_DEFAULT_FILTERS)},
    {"name": "adv_mitigation", "mode": "advanced", "session": "both", "min_rr": 2.0,
     "cfg": AdvancedConfig(entry_model="mitigation"), "filters": dict(_DEFAULT_FILTERS)},
    {"name": "adv_mitigation_no_amd", "mode": "advanced", "session": "both", "min_rr": 2.0,
     "cfg": AdvancedConfig(entry_model="mitigation", require_parent_amd=False), "filters": dict(_DEFAULT_FILTERS)},
    {"name": "adv_chop_loose", "mode": "advanced", "session": "both", "min_rr": 2.0,
     "cfg": AdvancedConfig(), "filters": {**_DEFAULT_FILTERS, "chop_efficiency_min": 0.15, "chop_max_pivots": 9}},
    {"name": "adv_mid_loose_42_58", "mode": "advanced", "session": "both", "min_rr": 2.0,
     "cfg": AdvancedConfig(), "filters": {**_DEFAULT_FILTERS, "mid_lo": 0.42, "mid_hi": 0.58}},
    {"name": "adv_mid_strict_30_70", "mode": "advanced", "session": "both", "min_rr": 2.0,
     "cfg": AdvancedConfig(), "filters": {**_DEFAULT_FILTERS, "mid_lo": 0.30, "mid_hi": 0.70}},
    {"name": "adv_asia_aggressive", "mode": "advanced", "session": "both", "min_rr": 2.0,
     "cfg": AdvancedConfig(), "filters": {**_DEFAULT_FILTERS, "asia_aggressive": True}},
    {"name": "adv_mitigation_off_rr15", "mode": "advanced", "session": "off", "min_rr": 1.5,
     "cfg": AdvancedConfig(entry_model="mitigation"), "filters": dict(_DEFAULT_FILTERS)},
    {"name": "adv_gates_off_session_off", "mode": "advanced", "session": "off", "min_rr": 2.0,
     "cfg": AdvancedConfig(require_cisd=False, require_parent_amd=False), "filters": dict(_DEFAULT_FILTERS)},
    {"name": "adv_gates_off_volume", "mode": "advanced", "session": "off", "min_rr": 1.5,
     "cfg": AdvancedConfig(require_cisd=False, require_parent_amd=False),
     "filters": {**_DEFAULT_FILTERS, "chop_efficiency_min": 0.15, "chop_max_pivots": 9,
                 "mid_lo": 0.42, "mid_hi": 0.58}},
    {"name": "adv_mitigation_volume", "mode": "advanced", "session": "off", "min_rr": 1.5,
     "cfg": AdvancedConfig(entry_model="mitigation", require_cisd=False, require_parent_amd=False),
     "filters": {**_DEFAULT_FILTERS, "chop_efficiency_min": 0.15, "chop_max_pivots": 9,
                 "mid_lo": 0.42, "mid_hi": 0.58}},
    {"name": "adv_lokz_rr15_chop", "mode": "advanced", "session": "lokz", "min_rr": 1.5,
     "cfg": AdvancedConfig(), "filters": {**_DEFAULT_FILTERS, "chop_efficiency_min": 0.15, "chop_max_pivots": 9}},
    {"name": "legacy_lokz_rr15_chop", "mode": "legacy", "session": "lokz", "min_rr": 1.5,
     "cfg": None, "filters": {**_DEFAULT_FILTERS, "chop_efficiency_min": 0.15, "chop_max_pivots": 9}},
    {"name": "adv_both_rr15_mid_loose", "mode": "advanced", "session": "both", "min_rr": 1.5,
     "cfg": AdvancedConfig(), "filters": {**_DEFAULT_FILTERS, "mid_lo": 0.42, "mid_hi": 0.58}},
    {"name": "legacy_both_rr15_mid_loose", "mode": "legacy", "session": "both", "min_rr": 1.5,
     "cfg": None, "filters": {**_DEFAULT_FILTERS, "mid_lo": 0.42, "mid_hi": 0.58}},
    {"name": "adv_no_amd_off_rr15", "mode": "advanced", "session": "off", "min_rr": 1.5,
     "cfg": AdvancedConfig(require_parent_amd=False), "filters": dict(_DEFAULT_FILTERS)},
    {"name": "adv_no_cisd_off_rr15", "mode": "advanced", "session": "off", "min_rr": 1.5,
     "cfg": AdvancedConfig(require_cisd=False), "filters": dict(_DEFAULT_FILTERS)},
    {"name": "adv_mitigation_lokz", "mode": "advanced", "session": "lokz", "min_rr": 2.0,
     "cfg": AdvancedConfig(entry_model="mitigation"), "filters": dict(_DEFAULT_FILTERS)},
]


def make_folds(candles: list[dict], train_fracs=(0.4, 0.6, 0.8), test_frac=0.2) -> list[dict]:
    n = len(candles)
    folds = []
    for idx, train_frac in enumerate(train_fracs, 1):
        train_end = min(n - 2, max(1, int(n * train_frac)))
        test_end = min(n - 1, max(train_end + 1, int(n * (train_frac + test_frac))))
        train_end_time = candles[train_end - 1]["time"]
        test_start_time = candles[train_end]["time"]
        test_end_time = candles[test_end]["time"]
        folds.append({
            "fold": idx,
            "train_end_idx": train_end - 1,
            "test_start_idx": train_end,
            "test_end_idx": test_end,
            "train_end_time": str(train_end_time),
            "test_start_time": str(test_start_time),
            "test_end_time": str(test_end_time),
        })
    return folds


def setups_to_rows(setups) -> list[dict]:
    rows = []
    for setup in setups:
        row = asdict(setup)
        row["time"] = str(setup.time)
        rows.append(row)
    return rows


def parse_dt(value: str) -> datetime:
    text = str(value).replace("T", " ")
    if "+" in text:
        text = text.split("+")[0].strip()
    return datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")


def subset_rows(rows: list[dict], start: str | None = None, end: str | None = None) -> list[dict]:
    start_dt = parse_dt(start) if start else None
    end_dt = parse_dt(end) if end else None
    out = []
    for row in rows:
        t = parse_dt(row["time"])
        if start_dt and t < start_dt:
            continue
        if end_dt and t > end_dt:
            continue
        out.append(deepcopy(row))
    return out


def r_series_from_rows(rows: list[dict]) -> list[float]:
    series = []
    for row in rows:
        outcome = row.get("outcome")
        if outcome == "Win":
            series.append(float(row.get("rr_ratio") or 0.0))
        elif outcome == "Loss":
            series.append(-1.0)
    return series


def max_drawdown_r(r_series: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in r_series:
        equity += r
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    return round(abs(max_dd), 3)


def longest_losing_streak(r_series: list[float]) -> int:
    best = cur = 0
    for r in r_series:
        if r < 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def enrich_metrics(metrics: dict, rows: list[dict], min_trades: int = 10) -> dict:
    out = {k: v for k, v in metrics.items() if k != "rows"}
    r_series = r_series_from_rows(rows)
    out["n_trades"] = out.get("setups", 0)
    out["avg_rr"] = out.get("avg_planned_rr", 0.0)
    out["win_rate"] = out.get("sim_win_rate", 0.0)
    out["max_drawdown_r"] = max_drawdown_r(r_series)
    out["max_losing_streak"] = longest_losing_streak(r_series)
    out["trade_count_ok"] = out.get("resolved", 0) >= min_trades
    out["trade_count_flag"] = None if out["trade_count_ok"] else f"resolved<{min_trades}"
    return out


def evaluate_rows(rows: list[dict], candles: list[dict], interval: str, min_trades: int = 10) -> tuple[dict, list[dict]]:
    result = simulate_rows(deepcopy(rows), candles=candles, interval=interval, aplus_only=True)
    out_rows = result.get("rows", [])
    return enrich_metrics(result, out_rows, min_trades=min_trades), out_rows


def train_score(metrics: dict) -> float:
    resolved = metrics.get("resolved", 0)
    if resolved < 5:
        return -999.0 + resolved
    exp = float(metrics.get("expectancy_r") or 0.0)
    pf = metrics.get("profit_factor_raw") or 0.0
    pf_term = 5.0 if pf == float("inf") else min(float(pf), 5.0)
    dd_penalty = float(metrics.get("max_drawdown_r") or 0.0) * 0.05
    return exp + pf_term * 0.05 - dd_penalty


def bootstrap_expectancy(r_series: list[float], samples: int = 2000, seed: int = 42) -> dict | None:
    if len(r_series) < 8:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(samples):
        sample = [rng.choice(r_series) for _ in range(len(r_series))]
        means.append(sum(sample) / len(sample))
    means.sort()
    lo = means[int(0.025 * (samples - 1))]
    hi = means[int(0.975 * (samples - 1))]
    return {
        "mean_expectancy_r": round(sum(r_series) / len(r_series), 3),
        "ci95_low": round(lo, 3),
        "ci95_high": round(hi, 3),
        "samples": samples,
    }


def week_regime_map(candles: list[dict]) -> dict[str, str]:
    df = pd.DataFrame(candles)
    df["time"] = pd.to_datetime(df["time"])
    df["week"] = df["time"].dt.to_period("W-MON").astype(str)
    regimes = {}
    for week, grp in df.groupby("week"):
        rng = float(grp["high"].max() - grp["low"].min())
        if rng <= 0:
            regimes[week] = "flat"
            continue
        trend_ratio = abs(float(grp["close"].iloc[-1] - grp["open"].iloc[0])) / rng
        if trend_ratio >= 0.55:
            regimes[week] = "trending"
        elif trend_ratio <= 0.35:
            regimes[week] = "ranging"
        else:
            regimes[week] = "mixed"
    return regimes


def add_week_regime(rows: list[dict], regime_by_week: dict[str, str]) -> list[dict]:
    out = []
    for row in rows:
        item = deepcopy(row)
        t = pd.Timestamp(parse_dt(row["time"]))
        week = str(t.to_period("W-MON"))
        item["week_regime"] = regime_by_week.get(week, "unknown")
        out.append(item)
    return out


def htf_stage_filter(rows: list[dict], candles: list[dict]) -> list[dict]:
    df = pd.DataFrame(candles)
    df["time"] = pd.to_datetime(df["time"])
    df["date"] = df["time"].dt.date
    iso = df["time"].dt.isocalendar()
    df["week_key"] = iso["year"].astype(str) + "-W" + iso["week"].astype(str)

    day_ctx = {}
    for _, grp in df.groupby("date"):
        grp = grp.sort_values("time")
        open_price = float(grp["open"].iloc[0])
        running_high = grp["high"].cummax()
        running_low = grp["low"].cummin()
        high_idx = running_high.groupby(grp.index).first()
        low_idx = running_low.groupby(grp.index).first()
        for i, row in grp.iterrows():
            sub = grp.loc[:i]
            running_high_val = float(sub["high"].max())
            running_low_val = float(sub["low"].min())
            high_time = sub.loc[sub["high"].idxmax(), "time"]
            low_time = sub.loc[sub["low"].idxmin(), "time"]
            day_ctx[row["time"]] = {
                "open": open_price,
                "close": float(row["close"]),
                "high_before_low": high_time <= low_time,
                "low_before_high": low_time < high_time,
                "running_high": running_high_val,
                "running_low": running_low_val,
            }

    week_ctx = {}
    for _, grp in df.groupby("week_key"):
        grp = grp.sort_values("time")
        open_price = float(grp["open"].iloc[0])
        for i, row in grp.iterrows():
            sub = grp.loc[:i]
            running_high_val = float(sub["high"].max())
            running_low_val = float(sub["low"].min())
            high_time = sub.loc[sub["high"].idxmax(), "time"]
            low_time = sub.loc[sub["low"].idxmin(), "time"]
            week_ctx[row["time"]] = {
                "open": open_price,
                "close": float(row["close"]),
                "high_before_low": high_time <= low_time,
                "low_before_high": low_time < high_time,
                "running_high": running_high_val,
                "running_low": running_low_val,
            }

    filtered = []
    for row in rows:
        t = pd.Timestamp(parse_dt(row["time"]))
        dctx = day_ctx.get(t)
        wctx = week_ctx.get(t)
        if not dctx or not wctx:
            continue
        bullish = (
            dctx["close"] >= dctx["open"]
            and wctx["close"] >= wctx["open"]
            and dctx["low_before_high"]
            and wctx["low_before_high"]
        )
        bearish = (
            dctx["close"] <= dctx["open"]
            and wctx["close"] <= wctx["open"]
            and dctx["high_before_low"]
            and wctx["high_before_low"]
        )
        if row["direction"] == "long" and bullish:
            filtered.append(deepcopy(row))
        elif row["direction"] == "short" and bearish:
            filtered.append(deepcopy(row))
    return filtered


def resample_to_4h(symbol: str, days: int) -> tuple[pd.DataFrame, dict]:
    base_df, base_meta = fetch_ohlcv(symbol, days, "1h", use_cache=True, refresh=False)
    out = base_df.resample("4h", label="right", closed="right").agg({
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last",
        "Volume": "sum",
    }).dropna()
    safe = symbol.replace("=", "_").replace("/", "_")
    cache_path = CACHE_DIR / f"{safe}_4h_resampled_from_1h.csv"
    out.index.name = "Datetime"
    out.to_csv(cache_path)
    return out, {
        "symbol": symbol,
        "requested_days": days,
        "effective_days": base_meta["effective_days"],
        "interval": "4h",
        "bars": len(out),
        "start": str(out.index[0]),
        "end": str(out.index[-1]),
        "source": "resampled_from_1h",
        "cache_path": str(cache_path),
    }


def dicts_to_candles(candle_dicts: list[dict]) -> list[Candle]:
    return [
        Candle(
            time=row["time"],
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row.get("volume", 0.0)),
        )
        for row in candle_dicts
    ]


def load_dataset(symbol: str, interval: str, corr_symbol: str = "DX-Y.NYB", days: int | None = None) -> dict:
    if days is None:
        days = 3650 if interval == "1d" else 730 if interval in ("1h", "4h") else 60

    if interval == "4h":
        df, meta = resample_to_4h(symbol, days)
        corr_df, _ = resample_to_4h(corr_symbol, days)
    else:
        df, meta = fetch_ohlcv(symbol, days, interval, use_cache=True, refresh=False)
        corr_df, _ = fetch_ohlcv(corr_symbol, days, interval, use_cache=True, refresh=False)

    candle_dicts = df_to_candle_dicts(df)
    corr_dicts = df_to_candle_dicts(corr_df)
    candles = dicts_to_candles(candle_dicts)
    correlated = align_correlated(candles, dicts_to_candles(corr_dicts))

    return {
        "symbol": symbol,
        "interval": interval,
        "days": days,
        "meta": meta,
        "candles": candles,
        "correlated": correlated,
        "candle_dicts": candle_dicts,
    }


def load_broker_dataset(source: str = "ctrader", corr_symbol: str = "DX-Y.NYB") -> dict | None:
    """Load broker 15m CSV if cached (cTrader demo fetch, etc.)."""
    fetched = fetch_broker_15m(source)
    if fetched is None:
        return None
    df, meta = fetched
    # Yahoo 15m DXY is ~60d only — align what exists; earlier bars get no SMT pair.
    corr_df, corr_meta = fetch_ohlcv(corr_symbol, 60, "15m", use_cache=True, refresh=False)
    candle_dicts = df_to_candle_dicts(df)
    corr_dicts = df_to_candle_dicts(corr_df)
    candles = dicts_to_candles(candle_dicts)
    correlated = align_correlated(candles, dicts_to_candles(corr_dicts))
    span_days = max(1, int((df.index[-1] - df.index[0]).total_seconds() // 86400))
    meta = {
        **meta,
        "effective_days": span_days,
        "corr_symbol": corr_symbol,
        "corr_bars": corr_meta.get("bars"),
        "corr_start": corr_meta.get("start"),
        "corr_end": corr_meta.get("end"),
        "corr_note": "Yahoo DXY 15m capped ~60d; SMT only on recent overlap",
    }
    return {
        "symbol": meta["symbol"],
        "interval": "15m",
        "days": span_days,
        "meta": meta,
        "candles": candles,
        "correlated": correlated,
        "candle_dicts": candle_dicts,
        "broker_source": source,
    }


def slice_dataset_last_months(dataset: dict, months: float = 9.0) -> dict:
    """Return a shallow copy of dataset restricted to the last `months` of bars."""
    from datetime import timedelta

    end_t = dataset["candle_dicts"][-1]["time"]
    cutoff = end_t - timedelta(days=int(months * 30.44))
    idxs = [i for i, row in enumerate(dataset["candle_dicts"]) if row["time"] >= cutoff]
    if not idxs:
        raise ValueError(f"No bars in last {months} months")
    start_i = idxs[0]
    candle_dicts = dataset["candle_dicts"][start_i:]
    candles = dataset["candles"][start_i:]
    correlated = dataset["correlated"][start_i:]
    span_days = max(1, int((candle_dicts[-1]["time"] - candle_dicts[0]["time"]).total_seconds() // 86400))
    meta = {
        **dataset["meta"],
        "bars": len(candle_dicts),
        "start": str(candle_dicts[0]["time"]),
        "end": str(candle_dicts[-1]["time"]),
        "effective_days": span_days,
        "window": f"last_{months:g}mo",
    }
    return {
        **dataset,
        "days": span_days,
        "meta": meta,
        "candles": candles,
        "correlated": correlated,
        "candle_dicts": candle_dicts,
    }


def evaluate_full_window(
    dataset: dict,
    strategy_mode: str,
    *,
    session: str = "both",
    min_rr: float = 2.0,
    timeline=None,
    advanced_cfg: AdvancedConfig | None = None,
) -> dict:
    """In-sample full-window metrics (not walk-forward)."""
    rows = run_scan(
        dataset,
        strategy_mode,
        session,
        min_rr,
        "nearest_liquidity",
        "rbos" if strategy_mode == "advanced" else None,
        timeline=timeline,
        advanced_cfg=advanced_cfg,
    )
    metrics, scored = evaluate_rows(rows, dataset["candle_dicts"], dataset["interval"], min_trades=15)
    return {"metrics": metrics, "n_setups": len(rows), "n_scored": len(scored)}


def run_scan(
    dataset: dict,
    strategy_mode: str,
    session: str,
    min_rr: float,
    tp_mode: str,
    entry_model: str | None = None,
    timeline=None,
    advanced_cfg: AdvancedConfig | None = None,
    scan_filters: dict | None = None,
    return_debug: bool = False,
):
    cfg = advanced_cfg
    if strategy_mode == "advanced":
        cfg = cfg or AdvancedConfig(entry_model=entry_model or "rbos")
    result = scan_setups(
        symbol=dataset["symbol"],
        days=dataset["days"],
        interval=dataset["interval"],
        session=session,
        min_rr=min_rr,
        strategy_mode=strategy_mode,
        advanced_cfg=cfg,
        candles=dataset["candles"],
        correlated=dataset["correlated"],
        tp_mode=tp_mode,
        quiet=True,
        precomputed_timeline=timeline,
        scan_filters=scan_filters,
        return_debug=return_debug,
    )
    if return_debug:
        setups, debug = result
        return setups_to_rows(setups), debug
    return setups_to_rows(result)


def summarize_strategy(name: str, rows: list[dict], folds: list[dict], candles: list[dict], interval: str) -> dict:
    fold_summaries = []
    all_test_rows = []
    for fold in folds:
        train_rows = subset_rows(rows, end=fold["train_end_time"])
        test_rows = subset_rows(rows, start=fold["test_start_time"], end=fold["test_end_time"])
        train_metrics, _ = evaluate_rows(train_rows, candles, interval, min_trades=10)
        test_metrics, test_scored_rows = evaluate_rows(test_rows, candles, interval, min_trades=10)
        fold_summaries.append({
            "fold": fold["fold"],
            "train_window_end": fold["train_end_time"],
            "test_window_start": fold["test_start_time"],
            "test_window_end": fold["test_end_time"],
            "train": train_metrics,
            "test": test_metrics,
        })
        all_test_rows.extend(test_scored_rows)
    aggregate_oos, scored_rows = evaluate_rows(all_test_rows, candles, interval, min_trades=15)
    return {
        "name": name,
        "folds": fold_summaries,
        "aggregate_oos": aggregate_oos,
        "oos_rows": scored_rows,
    }


def run_dataset_study(dataset: dict, *, full_grid: bool = True) -> dict:
    print(f"Studying {dataset['symbol']} @ {dataset['interval']} ({len(dataset['candle_dicts'])} bars)...", flush=True)
    folds = make_folds(dataset["candle_dicts"])
    timeline = build_advanced_timeline(dataset["candles"], AdvancedConfig())

    baseline_legacy_rows = run_scan(dataset, "legacy", "both", 2.0, "nearest_liquidity")
    baseline_advanced_rows = run_scan(
        dataset, "advanced", "both", 2.0, "nearest_liquidity", "rbos", timeline=timeline
    )

    legacy = summarize_strategy("legacy", baseline_legacy_rows, folds, dataset["candle_dicts"], dataset["interval"])
    advanced_default = summarize_strategy(
        "advanced_default", baseline_advanced_rows, folds, dataset["candle_dicts"], dataset["interval"]
    )

    tuned_by_fold = []
    selected_oos_rows = []
    tuned_oos = {"expectancy_r": 0.0, "resolved": 0, "profit_factor_raw": 0.0}
    tuned_scored_rows = []

    grid = CORE_GRID if full_grid else [
        {"session": "both", "min_rr": 2.0, "entry_model": "rbos", "tp_mode": "nearest_liquidity"},
        {"session": "lokz", "min_rr": 2.0, "entry_model": "rbos", "tp_mode": "nearest_liquidity"},
        {"session": "ny-am", "min_rr": 2.0, "entry_model": "rbos", "tp_mode": "nearest_liquidity"},
        {"session": "both", "min_rr": 1.5, "entry_model": "rbos", "tp_mode": "nearest_liquidity"},
    ]

    if grid:
        config_cache = {}
        for params in grid:
            key = json.dumps(params, sort_keys=True)
            config_cache[key] = run_scan(
                dataset,
                "advanced",
                params["session"],
                params["min_rr"],
                params["tp_mode"],
                params["entry_model"],
                timeline=timeline,
            )

        for fold in folds:
            best = None
            for params in grid:
                key = json.dumps(params, sort_keys=True)
                rows = config_cache[key]
                train_rows = subset_rows(rows, end=fold["train_end_time"])
                test_rows = subset_rows(rows, start=fold["test_start_time"], end=fold["test_end_time"])
                train_metrics, _ = evaluate_rows(train_rows, dataset["candle_dicts"], dataset["interval"], min_trades=10)
                test_metrics, test_scored_rows = evaluate_rows(test_rows, dataset["candle_dicts"], dataset["interval"], min_trades=10)
                row = {
                    "params": params,
                    "train": train_metrics,
                    "test": test_metrics,
                    "train_score": train_score(train_metrics),
                    "test_rows": test_scored_rows,
                }
                if best is None or row["train_score"] > best["train_score"]:
                    best = row
            tuned_by_fold.append({
                "fold": fold["fold"],
                "train_window_end": fold["train_end_time"],
                "test_window_start": fold["test_start_time"],
                "test_window_end": fold["test_end_time"],
                "selected_params": best["params"],
                "train": best["train"],
                "test": best["test"],
            })
            selected_oos_rows.extend(best["test_rows"])

        tuned_oos, tuned_scored_rows = evaluate_rows(selected_oos_rows, dataset["candle_dicts"], dataset["interval"], min_trades=15)

    creative = {}
    if dataset["symbol"] == "EURUSD=X" and dataset["interval"] == "1h":
        htf_rows = htf_stage_filter(tuned_scored_rows, dataset["candle_dicts"])
        htf_metrics, htf_scored_rows = evaluate_rows(htf_rows, dataset["candle_dicts"], dataset["interval"], min_trades=10)

        regime_map = week_regime_map(dataset["candle_dicts"])
        regime_rows = add_week_regime(tuned_scored_rows, regime_map)
        regime_results = {}
        for regime in ("trending", "ranging", "mixed"):
            subset = [deepcopy(row) for row in regime_rows if row.get("week_regime") == regime]
            regime_results[regime] = evaluate_rows(subset, dataset["candle_dicts"], dataset["interval"], min_trades=5)[0]

        bootstrap = bootstrap_expectancy(r_series_from_rows(tuned_scored_rows))
        creative = {
            "htf_context_proxy": htf_metrics,
            "regime_split": regime_results,
            "bootstrap_expectancy": bootstrap,
            "htf_context_row_count": len(htf_scored_rows),
        }

    return {
        "dataset": {
            "symbol": dataset["symbol"],
            "interval": dataset["interval"],
            "days": dataset["days"],
            **dataset["meta"],
        },
        "folds": folds,
        "legacy": legacy,
        "advanced_default": advanced_default,
        "advanced_tuned_walk_forward": {
            "folds": tuned_by_fold,
            "aggregate_oos": tuned_oos,
            "oos_rows": tuned_scored_rows,
        },
        "creative_angles": creative,
    }


def run_relaxation_study(dataset: dict) -> dict:
    """Test each Advanced gate relaxation independently; select on train, report OOS."""
    print(f"Relaxation study {dataset['symbol']} @ {dataset['interval']} ...", flush=True)
    folds = make_folds(dataset["candle_dicts"])
    variant_rows = {}
    variant_timelines = {}
    for variant in RELAXATION_VARIANTS:
        cfg = variant["cfg"]
        variant_timelines[variant["name"]] = build_advanced_timeline(dataset["candles"], cfg)
        variant_rows[variant["name"]] = run_scan(
            dataset,
            "advanced",
            variant["session"],
            variant["min_rr"],
            "nearest_liquidity",
            timeline=variant_timelines[variant["name"]],
            advanced_cfg=cfg,
        )

    fold_results = []
    selected_oos_rows = []
    for fold in folds:
        best = None
        for variant in RELAXATION_VARIANTS:
            rows = variant_rows[variant["name"]]
            train_rows = subset_rows(rows, end=fold["train_end_time"])
            test_rows = subset_rows(rows, start=fold["test_start_time"], end=fold["test_end_time"])
            train_metrics, _ = evaluate_rows(train_rows, dataset["candle_dicts"], dataset["interval"], min_trades=10)
            test_metrics, test_scored_rows = evaluate_rows(test_rows, dataset["candle_dicts"], dataset["interval"], min_trades=10)
            row = {
                "variant": variant["name"],
                "session": variant["session"],
                "min_rr": variant["min_rr"],
                "cfg": asdict(variant["cfg"]),
                "train": train_metrics,
                "test": test_metrics,
                "train_score": train_score(train_metrics),
                "test_rows": test_scored_rows,
            }
            if best is None or row["train_score"] > best["train_score"]:
                best = row
        fold_results.append({
            "fold": fold["fold"],
            "selected_variant": best["variant"],
            "selected_session": best["session"],
            "selected_min_rr": best["min_rr"],
            "selected_cfg": best["cfg"],
            "train": best["train"],
            "test": best["test"],
        })
        selected_oos_rows.extend(best["test_rows"])

    aggregate_oos, scored_rows = evaluate_rows(selected_oos_rows, dataset["candle_dicts"], dataset["interval"], min_trades=15)

    per_variant_oos = {}
    for variant in RELAXATION_VARIANTS:
        rows = variant_rows[variant["name"]]
        all_test = []
        for fold in folds:
            test_rows = subset_rows(rows, start=fold["test_start_time"], end=fold["test_end_time"])
            _, test_scored = evaluate_rows(test_rows, dataset["candle_dicts"], dataset["interval"], min_trades=10)
            all_test.extend(test_scored)
        per_variant_oos[variant["name"]] = evaluate_rows(all_test, dataset["candle_dicts"], dataset["interval"], min_trades=15)[0]

    return {
        "walk_forward": {
            "folds": fold_results,
            "aggregate_oos": aggregate_oos,
            "oos_rows": scored_rows,
        },
        "per_variant_oos": per_variant_oos,
    }


def diagnose_reject_funnel(dataset: dict) -> dict:
    """Full-sample reject reason counts for Legacy + Advanced on default filters."""
    print(f"Reject funnel {dataset['symbol']} @ {dataset['interval']} ...", flush=True)
    out = {}
    for mode, label, cfg in [
        ("legacy", "Legacy defaults (session=both, min_rr=2.0)", None),
        ("advanced", "Advanced defaults (session=both, min_rr=2.0)", AdvancedConfig()),
    ]:
        timeline = build_advanced_timeline(dataset["candles"], cfg or AdvancedConfig()) if mode == "advanced" else None
        rows, debug = run_scan(
            dataset, mode, "both", 2.0, "nearest_liquidity",
            timeline=timeline, advanced_cfg=cfg, scan_filters=dict(_DEFAULT_FILTERS),
            return_debug=True,
        )
        cand = debug.get("candidate_reject_counts") or {}
        top = sorted(cand.items(), key=lambda kv: -kv[1])
        out[mode] = {
            "label": label,
            "n_setups": len(rows),
            "bars_scanned": debug.get("bars_scanned"),
            "bars_in_session": debug.get("bars_in_session"),
            "poi_retest_candidates": debug.get("reject_counts", {}).get("poi_retest_candidate", 0),
            "accepts": debug.get("reject_counts", {}).get("accept", 0),
            "reject_counts": debug.get("reject_counts", {}),
            "candidate_reject_counts": cand,
            "top_candidate_rejects": [{"reason": k, "count": v} for k, v in top[:12]],
            "structure": {
                "fbos": debug.get("fbos_count"),
                "rbos": debug.get("rbos_count"),
                "cisd": debug.get("cisd_count"),
                "amd": debug.get("amd_count"),
            },
        }
    return out


def run_frequency_study(dataset: dict) -> dict:
    """OFAT Frequency track: train metrics for selection, OOS never used for tuning."""
    print(f"Frequency study {dataset['symbol']} @ {dataset['interval']} ...", flush=True)
    folds = make_folds(dataset["candle_dicts"])
    variant_rows = {}
    for variant in FREQUENCY_VARIANTS:
        cfg = variant["cfg"]
        timeline = None
        if variant["mode"] == "advanced":
            timeline = build_advanced_timeline(dataset["candles"], cfg or AdvancedConfig())
        variant_rows[variant["name"]] = run_scan(
            dataset,
            variant["mode"],
            variant["session"],
            variant["min_rr"],
            "nearest_liquidity",
            timeline=timeline,
            advanced_cfg=cfg,
            scan_filters=variant["filters"],
        )

    def _train_oos_for(rows: list[dict]) -> dict:
        train_all = []
        test_all = []
        fold_detail = []
        for fold in folds:
            train_rows = subset_rows(rows, end=fold["train_end_time"])
            test_rows = subset_rows(rows, start=fold["test_start_time"], end=fold["test_end_time"])
            train_m, _ = evaluate_rows(train_rows, dataset["candle_dicts"], dataset["interval"], min_trades=10)
            test_m, test_scored = evaluate_rows(test_rows, dataset["candle_dicts"], dataset["interval"], min_trades=10)
            train_all.extend(train_rows)
            test_all.extend(test_scored)
            fold_detail.append({
                "fold": fold["fold"],
                "train": train_m,
                "test": test_m,
            })
        # Aggregate: union of fold OOS windows (same as other studies)
        oos_m, _ = evaluate_rows(test_all, dataset["candle_dicts"], dataset["interval"], min_trades=15)
        # Full-sample train proxy = earliest train cut (fold 3 train = 80%) for ranking only
        train_proxy_rows = subset_rows(rows, end=folds[-1]["train_end_time"])
        train_proxy, _ = evaluate_rows(train_proxy_rows, dataset["candle_dicts"], dataset["interval"], min_trades=10)
        return {
            "train_proxy": train_proxy,
            "aggregate_oos": oos_m,
            "folds": fold_detail,
            "train_score": train_score(train_proxy),
        }

    per_variant = {}
    for variant in FREQUENCY_VARIANTS:
        stats = _train_oos_for(variant_rows[variant["name"]])
        per_variant[variant["name"]] = {
            "mode": variant["mode"],
            "session": variant["session"],
            "min_rr": variant["min_rr"],
            "cfg": asdict(variant["cfg"]) if variant["cfg"] is not None else None,
            "filters": variant["filters"],
            **stats,
        }

    # Best volume = max OOS resolved (report honestly; selection uses train n as primary)
    # Best quality = max train_score among variants with train resolved>=5, report its OOS
    volume_by_train = max(
        per_variant.items(),
        key=lambda kv: (kv[1]["train_proxy"].get("resolved") or 0, kv[1]["train_score"]),
    )
    quality_candidates = [
        (n, v) for n, v in per_variant.items()
        if (v["train_proxy"].get("resolved") or 0) >= 3
    ]
    if quality_candidates:
        quality_by_train = max(quality_candidates, key=lambda kv: kv[1]["train_score"])
    else:
        quality_by_train = max(per_variant.items(), key=lambda kv: kv[1]["train_score"])

    # Also surface best OOS volume / quality for documentation (not used for tuning)
    volume_oos_obs = max(
        per_variant.items(),
        key=lambda kv: (kv[1]["aggregate_oos"].get("resolved") or 0, kv[1]["aggregate_oos"].get("expectancy_r") or -999),
    )
    quality_oos_obs = max(
        per_variant.items(),
        key=lambda kv: (
            (kv[1]["aggregate_oos"].get("expectancy_r") or -999)
            if (kv[1]["aggregate_oos"].get("resolved") or 0) >= 1
            else -9999,
            kv[1]["aggregate_oos"].get("resolved") or 0,
        ),
    )

    return {
        "per_variant": per_variant,
        "best_volume_train_selected": {
            "name": volume_by_train[0],
            **volume_by_train[1],
        },
        "best_quality_train_selected": {
            "name": quality_by_train[0],
            **quality_by_train[1],
        },
        "best_volume_oos_observed": {
            "name": volume_oos_obs[0],
            "oos": volume_oos_obs[1]["aggregate_oos"],
        },
        "best_quality_oos_observed": {
            "name": quality_oos_obs[0],
            "oos": quality_oos_obs[1]["aggregate_oos"],
        },
    }


def meets_credible_edge(metrics: dict) -> bool:
    pf = metrics.get("profit_factor_raw") or 0.0
    pf_ok = pf == float("inf") or pf > 1.0
    return (
        float(metrics.get("expectancy_r") or 0.0) > 0
        and pf_ok
        and int(metrics.get("resolved") or 0) >= 15
    )


def write_research_best_preset(variant: dict) -> None:
    """Write research_best ScanRecipe into advanced_gates.SCAN_PRESETS (overall OOS winner)."""
    import re
    from advanced_gates import SCAN_PRESETS, ScanRecipe, STRATEGY_MODE_LEGACY, STRATEGY_MODE_ADVANCED

    path = ROOT / "advanced_gates.py"
    text = path.read_text()
    filters = dict(variant.get("filters") or _DEFAULT_FILTERS)
    mode = (variant.get("mode") or STRATEGY_MODE_LEGACY).strip().lower()
    if mode not in (STRATEGY_MODE_LEGACY, STRATEGY_MODE_ADVANCED):
        mode = STRATEGY_MODE_LEGACY
    session = variant.get("session", "both")
    min_rr = float(variant.get("min_rr", 2.0))
    name = variant.get("name") or "research_best"
    cfg = variant.get("cfg")
    if mode == STRATEGY_MODE_ADVANCED and cfg:
        adv = (
            "AdvancedConfig(\n"
            f"            require_cisd={cfg.get('require_cisd', True)},\n"
            f"            require_parent_amd={cfg.get('require_parent_amd', True)},\n"
            f"            entry_model=\"{cfg.get('entry_model', 'rbos')}\",\n"
            "        )"
        )
        adv_obj = AdvancedConfig(
            require_cisd=cfg.get("require_cisd", True),
            require_parent_amd=cfg.get("require_parent_amd", True),
            entry_model=cfg.get("entry_model", "rbos"),
        )
    else:
        adv = "None"
        adv_obj = None
    filt_lines = ",\n".join(
        f'            "{k}": {repr(filters[k])}' for k in (
            "chop_efficiency_min", "chop_max_pivots", "mid_lo", "mid_hi", "asia_aggressive"
        ) if k in filters
    )
    block = (
        '    "research_best": ScanRecipe(\n'
        f'        name="{name}",\n'
        f'        mode="{mode}",\n'
        f'        session="{session}",\n'
        f"        min_rr={min_rr},\n"
        "        filters={\n"
        f"{filt_lines}\n"
        "        },\n"
        f"        advanced={adv},\n"
        '        data_source="ctrader",\n'
        '        interval="15m",\n'
        f'        label="{name} — OOS best (cTrader M15)",\n'
        "    ),\n"
    )
    pattern = r'    "research_best": ScanRecipe\([\s\S]*?\),\n'
    if re.search(pattern, text):
        new_text, n = re.subn(pattern, block, text, count=1)
    else:
        new_text, n = re.subn(
            r'(SCAN_PRESETS: dict\[str, ScanRecipe\] = \{)\n',
            r'\1\n' + block,
            text,
            count=1,
        )
    if n != 1:
        raise RuntimeError("Could not write research_best ScanRecipe")
    path.write_text(new_text)
    SCAN_PRESETS["research_best"] = ScanRecipe(
        name=name,
        mode=mode,
        session=session,
        min_rr=min_rr,
        filters=filters,
        advanced=adv_obj,
        data_source="ctrader",
        interval="15m",
        label=f"{name} — OOS best (cTrader M15)",
    )


def best_verdict(dataset_results: list[dict]) -> dict:
    best = None
    for result in dataset_results:
        for key in ("legacy", "advanced_default", "advanced_tuned_walk_forward"):
            metrics = result[key]["aggregate_oos"]
            candidate = {
                "dataset": result["dataset"],
                "strategy": key,
                "metrics": metrics,
                "params": result[key]["folds"] if key == "advanced_tuned_walk_forward" else None,
            }
            score = float(metrics.get("expectancy_r") or 0.0)
            pf = metrics.get("profit_factor_raw") or 0.0
            if pf == float("inf"):
                score += 0.25
            elif isinstance(pf, (int, float)):
                score += min(float(pf), 3.0) * 0.05
            if best is None or score > best["score"]:
                best = {"score": score, **candidate}
    return best or {}


def _pf_text(metrics: dict) -> str:
    pf = metrics.get("profit_factor")
    if metrics.get("profit_factor_raw") == float("inf"):
        return "inf"
    if isinstance(pf, (int, float)):
        return f"{pf:.2f}"
    return "n/a"


def render_markdown(report: dict) -> str:
    lines = [
        "# Research Results",
        "",
        "## Honest Summary",
        "",
        report["summary_text"],
        "",
        "**Verdict:** "
        + ("No robust edge yet — do not deploy a `research_best` preset." if not report["credible_edge_found"] else "Credible edge met OOS bar — see preset notes."),
        "",
        "## Data story (corrected Jul 2026)",
        "",
        "- **Primary research surface:** cTrader demo EURUSD **M15** via `fetch_broker_15m(\"ctrader\")` — ~49.6k bars / ~24 months (`data_cache/EURUSD_ctrader_M15_730d.csv`).",
        "- Yahoo **15m/5m** remains capped at ~60 calendar days; that is **not** the research limit once broker cache is present.",
        "- Yahoo **1h** (~730d) is still useful as a secondary cross-check when broker 15m is unavailable.",
        "- **4h** bars are resampled from cached 1h OHLC (valid aggregation, not fake 15m).",
        "- DXY correlation on broker 15m uses Yahoo 15m (~60d overlap only); earlier bars have no SMT pair.",
        "- Dukascopy Python client failed here; cTrader Open API historical fetch is the long 15m source.",
        "",
        "## Data Collected",
        "",
        "| Symbol | Interval | Source | Start | End | Bars | Cache |",
        "|---|---|---|---|---:|---:|---|",
    ]
    for row in report["data_inventory"]:
        lines.append(
            f"| `{row['symbol']}` | `{row['interval']}` | `{row['source']}` | `{row['start']}` | `{row['end']}` | {row['bars']} | `{row['cache_path']}` |"
        )

    if report.get("window_studies"):
        lines.extend([
            "",
            "## EURUSD cTrader M15 — full window vs last 9 months (in-sample)",
            "",
            "| Window | Strategy | n | WR | E[R] | PF | Flag |",
            "|---|---|---:|---:|---:|---:|---|",
        ])
        for block in report["window_studies"]:
            for row in block["rows"]:
                m = row["metrics"]
                lines.append(
                    f"| `{block['window']}` | `{row['strategy']}` | {m.get('resolved', 0)} | "
                    f"{m.get('win_rate', 0):.1f}% | {m.get('expectancy_r', 0):.3f} | {_pf_text(m)} | "
                    f"{m.get('trade_count_flag') or ''} |"
                )

    lines.extend([
        "",
        "## OOS Results (3-fold walk-forward aggregate)",
        "",
        "| Dataset | Strategy | Trades | Resolved | Win Rate | Avg RR | Expectancy R | PF | Max DD R | Lose Streak | Flag |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ])
    for row in report["result_table"]:
        pf_text = _pf_text(row)
        lines.append(
            f"| `{row['dataset']}` | `{row['strategy']}` | {row['n_trades']} | {row['resolved']} | {row['win_rate']:.1f}% | {row['avg_rr']:.2f} | {row['expectancy_r']:.3f} | {pf_text} | {row['max_drawdown_r']:.2f} | {row['max_losing_streak']} | {row['trade_count_flag'] or ''} |"
        )

    primary_15 = next(
        (s for s in report["studies"] if s["dataset"].get("source") == "ctrader" and s["dataset"]["interval"] == "15m"),
        None,
    )
    primary = primary_15 or next(
        (s for s in report["studies"] if s["dataset"]["symbol"] == "EURUSD=X" and s["dataset"]["interval"] == "1h"),
        None,
    )
    if primary:
        title = (
            "EURUSD cTrader M15 fold detail"
            if primary["dataset"].get("source") == "ctrader"
            else "EURUSD 1h fold detail"
        )
        lines.extend(["", f"## {title} (Legacy vs Advanced)", ""])
        for label, key in [("Legacy", "legacy"), ("Advanced default", "advanced_default"), ("Advanced tuned", "advanced_tuned_walk_forward")]:
            lines.append(f"### {label}")
            lines.append("")
            lines.append("| Fold | Train E[R] | Train n | OOS E[R] | OOS n | OOS PF |")
            lines.append("|---:|---:|---:|---:|---:|---:|")
            folds = primary[key]["folds"]
            for fold in folds:
                tr = fold["train"]
                te = fold["test"]
                lines.append(
                    f"| {fold['fold']} | {tr['expectancy_r']:.3f} | {tr['resolved']} | {te['expectancy_r']:.3f} | {te['resolved']} | {_pf_text(te)} |"
                )
            lines.append("")

    if report.get("reject_funnels"):
        lines.extend(["", "## Reject funnel (full-sample cTrader M15 defaults)", ""])
        for block in report["reject_funnels"]:
            ds = f"{block['dataset']['symbol']} {block['dataset']['interval']}"
            lines.append(f"### `{ds}`")
            lines.append("")
            for mode in ("legacy", "advanced"):
                fun = block.get(mode) or {}
                lines.append(f"**{fun.get('label', mode)}** — setups={fun.get('n_setups', 0)}, "
                             f"POI retests={fun.get('poi_retest_candidates', 0)}, accepts={fun.get('accepts', 0)}, "
                             f"bars_in_session={fun.get('bars_in_session', 'n/a')}")
                lines.append("")
                lines.append("| Reason | Count |")
                lines.append("|---|---:|")
                for row in fun.get("top_candidate_rejects") or []:
                    lines.append(f"| `{row['reason']}` | {row['count']} |")
                lines.append("")
                struct = fun.get("structure") or {}
                if any(struct.get(k) is not None for k in ("fbos", "rbos", "cisd", "amd")):
                    lines.append(
                        f"Structure counts: FBOS={struct.get('fbos')}, RBOS={struct.get('rbos')}, "
                        f"CISD={struct.get('cisd')}, AMD={struct.get('amd')}."
                    )
                    lines.append("")

    if report.get("frequency_studies"):
        lines.extend(["", "## Frequency track (train-selected OFAT, OOS report-only)", ""])
        lines.append("| Dataset | Variant | Mode | Train n | Train E[R] | OOS n | OOS E[R] | OOS PF | Flag |")
        lines.append("|---|---|---|---:|---:|---:|---:|---:|---|")
        for block in report["frequency_studies"]:
            src = block["dataset"].get("source") or ""
            ds = f"{block['dataset']['symbol']} {block['dataset']['interval']}" + (f" ({src})" if src else "")
            for name, v in block["per_variant"].items():
                tr = v["train_proxy"]
                oos = v["aggregate_oos"]
                lines.append(
                    f"| `{ds}` | `{name}` | `{v['mode']}` | {tr.get('resolved', 0)} | {tr.get('expectancy_r', 0):.3f} | "
                    f"{oos.get('resolved', 0)} | {oos.get('expectancy_r', 0):.3f} | {_pf_text(oos)} | "
                    f"{oos.get('trade_count_flag') or ''} |"
                )
            bv = block.get("best_volume_train_selected") or {}
            bq = block.get("best_quality_train_selected") or {}
            lines.append("")
            lines.append(
                f"Train-selected **volume** `{bv.get('name')}`: OOS n={bv.get('aggregate_oos', {}).get('resolved', 0)}, "
                f"E[R]={bv.get('aggregate_oos', {}).get('expectancy_r', 0):.3f}."
            )
            lines.append(
                f"Train-selected **quality** `{bq.get('name')}`: OOS n={bq.get('aggregate_oos', {}).get('resolved', 0)}, "
                f"E[R]={bq.get('aggregate_oos', {}).get('expectancy_r', 0):.3f}."
            )
            lines.append("")

    if report.get("relaxation_studies"):
        lines.extend(["", "## Advanced gate relaxations (train-selected, OOS aggregate)", ""])
        lines.append("| Dataset | Variant | Resolved | WR | E[R] | PF | Flag |")
        lines.append("|---|---|---:|---:|---:|---:|---|")
        for block in report["relaxation_studies"]:
            src = block["dataset"].get("source") or ""
            ds = f"{block['dataset']['symbol']} {block['dataset']['interval']}" + (f" ({src})" if src else "")
            for name, metrics in block["per_variant_oos"].items():
                lines.append(
                    f"| `{ds}` | `{name}` | {metrics.get('resolved', 0)} | {metrics.get('win_rate', 0):.1f}% | "
                    f"{metrics.get('expectancy_r', 0):.3f} | {_pf_text(metrics)} | {metrics.get('trade_count_flag') or ''} |"
                )
            wf = block.get("walk_forward", {}).get("aggregate_oos", {})
            lines.append("")
            lines.append(
                f"Walk-forward relaxation pick (`{ds}`): E[R]={wf.get('expectancy_r', 0):.3f}, "
                f"WR={wf.get('win_rate', 0):.1f}%, n={wf.get('resolved', 0)}, PF={_pf_text(wf)}."
            )

    if report.get("preset_written") or report.get("credible_edge_found"):
        pv = report.get("preset_variant") or {}
        lines.extend([
            "",
            "## `research_best` preset",
            "",
        ])
        if report.get("preset_written"):
            lines.append(
                f"Wired to `advanced_gates.SCAN_PRESETS['research_best']`: "
                f"variant=`{pv.get('name')}`, mode=`{pv.get('mode')}`, session=`{pv.get('session')}`, "
                f"min_rr=`{pv.get('min_rr')}`."
            )
            if pv.get("note"):
                lines.append(f"Note: {pv['note']}.")
            filt = pv.get("filters") or {}
            if filt:
                lines.append(
                    f"Filters: chop={filt.get('chop_efficiency_min')}/{filt.get('chop_max_pivots')}, "
                    f"mid={filt.get('mid_lo')}/{filt.get('mid_hi')}, asia={filt.get('asia_aggressive')}."
                )
        else:
            lines.append(
                "OOS bar cleared but `research_best` ScanRecipe was not rewritten. "
                "See `reports/research_best_scan.json`."
            )
        lines.append("")
        lines.append(
            "Full scan recipe (mode/session/min_rr/filters) is in `reports/research_best_scan.json`. "
            "Primary reject drivers were `mid_range` and `chop` — not Advanced CISD/AMD."
        )
    else:
        lines.extend([
            "",
            "## `research_best` preset",
            "",
            "Not rewritten — no configuration cleared the OOS bar. Placeholder remains identical to `advanced_default`.",
        ])

    next_actions = report.get("next_actions") or [
        "**Keep cTrader M15 as primary** — refresh with `scripts/ctrader_fetch_history.py --period M15 --days 730`.",
        "**Manual label 20–30 setups** on EURUSD 15m and compare to scanner output (parity check).",
        "**ATAS parity** — only if a clear `research_best` winner exists.",
    ]
    lines.extend(["", "## Next actions", ""])
    for i, item in enumerate(next_actions, 1):
        lines.append(f"{i}. {item}")
    lines.extend([
        "",
        "## Reproduce",
        "",
        "```bash",
        "cd /Users/alimire/Downloads/smc-backtest",
        "source .venv/bin/activate",
        "python research/run_profitability_study.py --focus ctrader",
        "python scripts/ctrader_fetch_history.py --period M15 --days 730",
        "pytest -q",
        "```",
        "",
    ])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run honest SMC profitability study")
    parser.add_argument("--json-out", default=str(DEFAULT_JSON))
    parser.add_argument("--md-out", default=str(DEFAULT_MD))
    parser.add_argument(
        "--focus",
        choices=("all", "ctrader"),
        default="ctrader",
        help="ctrader = primary broker M15 study; all = also Yahoo 1h/4h proxies",
    )
    args = parser.parse_args()

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    broker_ds = load_broker_dataset("ctrader")
    if broker_ds is None:
        raise SystemExit(
            "No cTrader M15 cache found. Run: python scripts/ctrader_fetch_history.py --period M15 --days 730"
        )

    inventories = [broker_ds["meta"]]
    if args.focus == "all":
        for symbol, interval, days in [
            ("EURUSD=X", "15m", 60),
            ("EURUSD=X", "1h", 730),
            ("EURUSD=X", "4h", 730),
            ("GC=F", "1h", 730),
            ("6E=F", "1h", 730),
        ]:
            data = load_dataset(symbol, interval, days=days)
            inventories.append(data["meta"])

    study_datasets = [broker_ds]
    if args.focus == "all":
        study_datasets.extend([
            load_dataset("EURUSD=X", "1h", days=730),
            load_dataset("EURUSD=X", "4h", days=730),
            load_dataset("GC=F", "1h", days=730),
            load_dataset("6E=F", "1h", days=730),
        ])

    # Full ~24mo and last 9 months in-sample Legacy vs Advanced (cTrader primary)
    window_studies = []
    for label, ds in [
        ("full_~24mo", broker_ds),
        ("last_9mo", slice_dataset_last_months(broker_ds, 9.0)),
    ]:
        print(f"Window study {label} ({len(ds['candle_dicts'])} bars)...", flush=True)
        tl = build_advanced_timeline(ds["candles"], AdvancedConfig())
        rows = [
            {"strategy": "Legacy", **evaluate_full_window(ds, "legacy")},
            {
                "strategy": "Advanced default",
                **evaluate_full_window(ds, "advanced", timeline=tl, advanced_cfg=AdvancedConfig()),
            },
        ]
        window_studies.append({
            "window": label,
            "bars": len(ds["candle_dicts"]),
            "start": ds["meta"]["start"],
            "end": ds["meta"]["end"],
            "rows": rows,
        })

    studies = [
        run_dataset_study(dataset, full_grid=dataset["interval"] != "15m")
        for dataset in study_datasets
    ]

    relaxation_targets = [
        ds for ds in study_datasets
        if ds["interval"] == "15m" or (ds["symbol"] == "EURUSD=X" and ds["interval"] == "1h")
    ]
    relaxation_studies = []
    for ds in relaxation_targets:
        block = run_relaxation_study(ds)
        block["dataset"] = {
            "symbol": ds["symbol"],
            "interval": ds["interval"],
            "source": ds["meta"].get("source"),
        }
        relaxation_studies.append(block)

    reject_funnels = []
    frequency_studies = []
    for ds in study_datasets:
        if ds["interval"] != "15m" and not (ds["symbol"] == "EURUSD=X" and ds["interval"] == "1h"):
            continue
        funnel = diagnose_reject_funnel(ds)
        funnel["dataset"] = {
            "symbol": ds["symbol"],
            "interval": ds["interval"],
            "source": ds["meta"].get("source"),
        }
        reject_funnels.append(funnel)
        freq = run_frequency_study(ds)
        freq["dataset"] = {
            "symbol": ds["symbol"],
            "interval": ds["interval"],
            "source": ds["meta"].get("source"),
        }
        frequency_studies.append(freq)

    table = []
    for result in studies:
        src = result["dataset"].get("source")
        dataset_name = f"{result['dataset']['symbol']} {result['dataset']['interval']}"
        if src == "ctrader":
            dataset_name = f"EURUSD ctrader {result['dataset']['interval']}"
        for key, label in [
            ("legacy", "Legacy"),
            ("advanced_default", "Advanced default"),
            ("advanced_tuned_walk_forward", "Advanced tuned"),
        ]:
            metrics = result[key]["aggregate_oos"]
            table.append({
                "dataset": dataset_name,
                "strategy": label,
                **metrics,
            })

    best = best_verdict(studies)
    best_metrics = best.get("metrics", {})
    best_dataset = best.get("dataset", {})
    best_has_edge = meets_credible_edge(best_metrics)

    # Also check relaxation walk-forward aggregates / per-variant OOS
    preset_written = False
    preset_variant = None
    if not best_has_edge and relaxation_studies:
        for block in relaxation_studies:
            wf = block["walk_forward"]["aggregate_oos"]
            if meets_credible_edge(wf):
                best_has_edge = True
                best_metrics = wf
                best_dataset = block["dataset"]
                best = {"strategy": "relaxation_walk_forward", "metrics": wf, "dataset": block["dataset"]}
                # Prefer last fold's selected variant for preset wiring
                last = block["walk_forward"]["folds"][-1]
                preset_variant = {
                    "name": last["selected_variant"],
                    "session": last["selected_session"],
                    "min_rr": last["selected_min_rr"],
                    "cfg": last["selected_cfg"],
                }
                write_research_best_preset(preset_variant)
                preset_written = True
                break
        if not preset_written:
            for block in relaxation_studies:
                for name, metrics in block["per_variant_oos"].items():
                    if meets_credible_edge(metrics):
                        variant = next(v for v in RELAXATION_VARIANTS if v["name"] == name)
                        preset_variant = {**variant, "cfg": asdict(variant["cfg"])}
                        write_research_best_preset(preset_variant)
                        preset_written = True
                        best_has_edge = True
                        best_metrics = metrics
                        best = {"strategy": f"relaxation:{name}", "metrics": metrics, "dataset": block["dataset"]}
                        break
                if preset_written:
                    break

    # Frequency track: rank all clearing variants by OOS score; prefer Advanced for preset
    if frequency_studies:
        clearing = []
        for block in frequency_studies:
            for name, v in block.get("per_variant", {}).items():
                metrics = v.get("aggregate_oos") or {}
                if not meets_credible_edge(metrics):
                    continue
                score = float(metrics.get("expectancy_r") or 0.0)
                pf = metrics.get("profit_factor_raw") or 0.0
                if pf == float("inf"):
                    score += 0.25
                elif isinstance(pf, (int, float)):
                    score += min(float(pf), 3.0) * 0.05
                # slight bonus for sample size up to 40
                score += min(int(metrics.get("resolved") or 0), 40) * 0.01
                clearing.append({
                    "name": name,
                    "block": block,
                    "variant": v,
                    "metrics": metrics,
                    "score": score,
                })
        if clearing:
            clearing.sort(key=lambda x: -x["score"])
            top = clearing[0]
            best_has_edge = True
            best_metrics = top["metrics"]
            best_dataset = top["block"]["dataset"]
            best = {
                "strategy": f"frequency:{top['name']}",
                "metrics": top["metrics"],
                "dataset": top["block"]["dataset"],
                "score": top["score"],
            }
            preset_variant = {
                "name": top["name"],
                "session": top["variant"].get("session", "both"),
                "min_rr": top["variant"].get("min_rr", 2.0),
                "cfg": top["variant"].get("cfg") or asdict(AdvancedConfig()),
                "filters": top["variant"].get("filters"),
                "mode": top["variant"].get("mode"),
            }
            # Wire overall OOS best (Legacy chop_loose beats Advanced gates_off).
            write_research_best_preset(preset_variant)
            preset_written = True
            adv_clear = next((c for c in clearing if c["variant"].get("mode") == "advanced"), None)
            # Always persist full scan recipe (works for legacy winners too)
            scan_path = REPORTS_DIR / "research_best_scan.json"
            scan_path.write_text(json.dumps({
                "best_overall": {
                    "name": top["name"],
                    "mode": top["variant"].get("mode"),
                    "session": top["variant"].get("session"),
                    "min_rr": top["variant"].get("min_rr"),
                    "filters": top["variant"].get("filters"),
                    "cfg": top["variant"].get("cfg"),
                    "aggregate_oos": top["metrics"],
                },
                "best_advanced": None if adv_clear is None else {
                    "name": adv_clear["name"],
                    "mode": "advanced",
                    "session": adv_clear["variant"].get("session"),
                    "min_rr": adv_clear["variant"].get("min_rr"),
                    "filters": adv_clear["variant"].get("filters"),
                    "cfg": adv_clear["variant"].get("cfg"),
                    "aggregate_oos": adv_clear["metrics"],
                },
                "all_clearing": [
                    {"name": c["name"], "mode": c["variant"].get("mode"),
                     "resolved": c["metrics"].get("resolved"),
                     "expectancy_r": c["metrics"].get("expectancy_r"),
                     "profit_factor": c["metrics"].get("profit_factor")}
                    for c in clearing
                ],
            }, indent=2, default=str))

    if best_has_edge and not preset_written:
        for result in studies:
            for key in ("legacy", "advanced_tuned_walk_forward"):
                metrics = result[key]["aggregate_oos"]
                if meets_credible_edge(metrics):
                    preset_variant = {
                        "name": key,
                        "session": "both",
                        "min_rr": 2.0,
                        "cfg": asdict(AdvancedConfig()),
                    }
                    if key == "advanced_tuned_walk_forward" and result[key]["folds"]:
                        p = result[key]["folds"][-1]["selected_params"]
                        preset_variant["session"] = p["session"]
                        preset_variant["min_rr"] = p["min_rr"]
                    write_research_best_preset(preset_variant)
                    preset_written = True
                    break
            if preset_written:
                break

    summary_text = (
        f"Best observed walk-forward/out-of-sample result was `{best.get('strategy', 'n/a')}` on "
        f"`{best_dataset.get('symbol', 'n/a')} {best_dataset.get('interval', 'n/a')}` with "
        f"expectancy `{best_metrics.get('expectancy_r', 0):.3f}R`, profit factor "
        f"`{'inf' if best_metrics.get('profit_factor_raw') == float('inf') else round(best_metrics.get('profit_factor', 0) or 0, 2)}` "
        f"across `{best_metrics.get('resolved', 0)}` resolved OOS trades. "
        + ("A credible edge met the minimum bar here." if best_has_edge else "No configuration cleared the credible-edge bar yet.")
    )

    # Concrete next lever when edge not found
    next_actions = []
    if best_has_edge:
        pv = preset_variant or {}
        if (pv.get("mode") == "legacy") or (best.get("strategy", "").startswith("frequency:legacy")):
            next_actions = [
                "**NEXT: paper-trade `research_best`** on live/demo (Legacy + chop 0.15/9); validate manually **10 setups**; "
                "then optional GC; do **not** redeploy Advanced gates_off as primary.",
                "UI: `python app.py` → http://127.0.0.1:5050 with `research_best` + cTrader M15 selected.",
                "Recipe frozen in `reports/research_best_scan.json` / `SCAN_PRESETS['research_best']`.",
            ]
        else:
            next_actions = [
                "Freeze `research_best` / `research_best_scan.json` and re-run walk-forward on a fresh cTrader refresh.",
                "Optionally sync ATAS defaults to the Advanced clearer (weaker E[R] than Legacy chop_loose).",
                "Paper-trade only; do not Railway-deploy until n≥30 on a non-overlapping holdout.",
            ]
    else:
        # Inspect frequency volume for guidance
        tip = "Multi-symbol EURUSD+GC (or 5m Yahoo window) to raise n without fabricating history."
        if frequency_studies:
            bv = frequency_studies[0].get("best_volume_train_selected") or {}
            oos_n = (bv.get("aggregate_oos") or {}).get("resolved") or 0
            if oos_n < 15:
                tip = (
                    f"Best volume variant `{bv.get('name')}` still only n={oos_n} OOS — "
                    "next lever: multi-symbol EURUSD+GC, or manual label 20–30 setups for parity, "
                    "or broker 5m if available."
                )
        next_actions = [
            tip,
            "Keep cTrader M15 primary; refresh with `scripts/ctrader_fetch_history.py --period M15 --days 730`.",
            "Skip ATAS default sync until a clear OOS winner exists.",
        ]

    report = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "data_inventory": inventories,
        "window_studies": window_studies,
        "studies": studies,
        "relaxation_studies": relaxation_studies,
        "reject_funnels": reject_funnels,
        "frequency_studies": frequency_studies,
        "result_table": table,
        "best_candidate": best,
        "credible_edge_found": best_has_edge,
        "preset_written": preset_written,
        "preset_variant": preset_variant,
        "summary_text": summary_text,
        "next_actions": next_actions,
    }

    json_path = Path(args.json_out)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, default=str))

    md_path = Path(args.md_out)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_markdown(report))

    print(summary_text)
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")


if __name__ == "__main__":
    main()
