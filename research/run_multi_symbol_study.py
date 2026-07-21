#!/usr/bin/env python3
"""Multi-symbol walk-forward evaluation of the frozen `research_best` recipe.

Honesty rules:
- The recipe (Legacy chop_loose: chop 0.15/9, session both, min_rr 2.0) was
  frozen on EURUSD before this study. NO per-symbol retuning happens here.
- 3-fold walk-forward windows (train 40/60/80% -> next 20% test); only the
  union of test windows is reported as OOS.
- Include bar for the DEMO bot: OOS resolved n >= 10, E[R] > 0, PF > 1.

Data source: cTrader Fusion demo M15 CSVs in data_cache
(`{SYM}_ctrader_M15_*.csv`, newest file per symbol wins).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

from advanced_gates import get_scan_preset  # noqa: E402
from data_fetch import df_to_candle_dicts, fetch_broker_15m, fetch_ohlcv  # noqa: E402
from run_profitability_study import (  # noqa: E402
    evaluate_rows,
    make_folds,
    setups_to_rows,
    subset_rows,
)
from smc_detector import Candle, align_correlated, scan_setups  # noqa: E402

REPORTS_DIR = ROOT / "reports"
DEFAULT_SYMBOLS = (
    "EURUSD",
    "XAUUSD",
    "GBPUSD",
    "USDJPY",
    "AUDUSD",
    "USDCAD",
    "NZDUSD",
    "USDCHF",
    "USDX",
)
MIN_OOS_TRADES = 10


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


def load_symbol_dataset(symbol: str) -> dict | None:
    fetched = fetch_broker_15m("ctrader", symbol=symbol)
    if fetched is None:
        return None
    df, meta = fetched
    candle_dicts = df_to_candle_dicts(df)
    candles = dicts_to_candles(candle_dicts)
    # DXY SMT pair: Yahoo 15m (~60d only); earlier bars simply lack SMT,
    # matching how the original EURUSD research_best study was run.
    try:
        corr_df, _ = fetch_ohlcv("DX-Y.NYB", 60, "15m", use_cache=True, refresh=False)
        correlated = align_correlated(candles, dicts_to_candles(df_to_candle_dicts(corr_df)))
    except Exception:
        correlated = [None] * len(candles)
    return {
        "symbol": symbol,
        "meta": meta,
        "candles": candles,
        "correlated": correlated,
        "candle_dicts": candle_dicts,
    }


def pf_text(metrics: dict) -> str:
    if metrics.get("profit_factor_raw") == float("inf"):
        return "inf"
    pf = metrics.get("profit_factor")
    return f"{pf:.2f}" if isinstance(pf, (int, float)) else "n/a"


def metrics_row(metrics: dict) -> dict:
    return {
        "n_setups": metrics.get("setups", 0),
        "resolved": metrics.get("resolved", 0),
        "win_rate": round(float(metrics.get("sim_win_rate") or metrics.get("win_rate") or 0.0), 1),
        "expectancy_r": round(float(metrics.get("expectancy_r") or 0.0), 3),
        "profit_factor": metrics.get("profit_factor"),
        "profit_factor_raw": metrics.get("profit_factor_raw"),
        "max_drawdown_r": metrics.get("max_drawdown_r"),
        "flag": metrics.get("trade_count_flag"),
    }


def passes_bar(metrics: dict) -> bool:
    pf = metrics.get("profit_factor_raw") or 0.0
    pf_ok = pf == float("inf") or float(pf) > 1.0
    return (
        int(metrics.get("resolved") or 0) >= MIN_OOS_TRADES
        and float(metrics.get("expectancy_r") or 0.0) > 0
        and pf_ok
    )


def study_symbol(symbol: str, recipe) -> dict:
    dataset = load_symbol_dataset(symbol)
    if dataset is None:
        return {"symbol": symbol, "status": "no_data", "reason": "no cTrader M15 cache found"}

    candle_dicts = dataset["candle_dicts"]
    interval = "15m"
    print(
        f"[{symbol}] {len(candle_dicts)} bars "
        f"{dataset['meta']['start']} -> {dataset['meta']['end']}",
        flush=True,
    )

    setups = scan_setups(
        symbol=f"{symbol}=X",
        days=730,
        interval=interval,
        session=recipe.session,
        min_rr=float(recipe.min_rr),
        strategy_mode=recipe.mode,
        advanced_cfg=recipe.advanced,
        scan_filters=dict(recipe.filters),
        candles=dataset["candles"],
        correlated=dataset["correlated"],
        quiet=True,
        sl_buffer_pips=5.0,
    )
    rows = setups_to_rows(setups)

    full_metrics, _ = evaluate_rows(rows, candle_dicts, interval, min_trades=MIN_OOS_TRADES)

    folds = make_folds(candle_dicts)
    fold_details = []
    oos_rows = []
    for fold in folds:
        test_rows = subset_rows(rows, start=fold["test_start_time"], end=fold["test_end_time"])
        test_metrics, scored = evaluate_rows(test_rows, candle_dicts, interval, min_trades=5)
        fold_details.append(
            {
                "fold": fold["fold"],
                "test_window": [fold["test_start_time"], fold["test_end_time"]],
                **metrics_row(test_metrics),
            }
        )
        oos_rows.extend(scored)
    oos_metrics, _ = evaluate_rows(oos_rows, candle_dicts, interval, min_trades=MIN_OOS_TRADES)

    return {
        "symbol": symbol,
        "status": "ok",
        "bars": len(candle_dicts),
        "data_start": dataset["meta"]["start"],
        "data_end": dataset["meta"]["end"],
        "cache_path": dataset["meta"]["cache_path"],
        "total_setups": len(rows),
        "full_sample": metrics_row(full_metrics),
        "folds": fold_details,
        "oos": metrics_row(oos_metrics),
        "passes_bar": passes_bar(oos_metrics),
    }


def render_markdown(report: dict) -> str:
    lines = [
        "## Multi-symbol OOS walk-forward (`research_best`, frozen params — no per-pair tuning)",
        "",
        f"Generated {report['generated_at']} — recipe `{report['recipe']}`; "
        f"include bar: OOS n>={MIN_OOS_TRADES}, E[R]>0, PF>1.",
        "",
        "| Symbol | Bars | Setups | OOS n | OOS WR | OOS E[R] | OOS PF | Full n | Full E[R] | Verdict |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for res in report["results"]:
        if res["status"] != "ok":
            lines.append(
                f"| `{res['symbol']}` | - | - | - | - | - | - | - | - | SKIPPED ({res['reason']}) |"
            )
            continue
        oos = res["oos"]
        full = res["full_sample"]
        pf = "inf" if oos["profit_factor_raw"] in (float("inf"), "inf") else (
            f"{oos['profit_factor']:.2f}" if isinstance(oos["profit_factor"], (int, float)) else "n/a"
        )
        verdict = res.get("verdict") or ("PASS" if res["passes_bar"] else "FAIL")
        lines.append(
            f"| `{res['symbol']}` | {res['bars']} | {res['total_setups']} | {oos['resolved']} | "
            f"{oos['win_rate']:.1f}% | {oos['expectancy_r']:.3f} | {pf} | "
            f"{full['resolved']} | {full['expectancy_r']:.3f} | {verdict} |"
        )
    lines.append("")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="*", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--json-out", default=str(REPORTS_DIR / "multi_symbol_study.json"))
    parser.add_argument("--md-out", default=str(REPORTS_DIR / "multi_symbol_study.md"))
    args = parser.parse_args()

    recipe = get_scan_preset("research_best")
    results = []
    for symbol in args.symbols:
        try:
            results.append(study_symbol(symbol.upper(), recipe))
        except Exception as exc:  # keep going; record why a symbol failed
            results.append({"symbol": symbol.upper(), "status": "error", "reason": str(exc)})

    for res in results:
        if res["status"] != "ok":
            res["verdict"] = "SKIPPED"
            continue
        if res["passes_bar"]:
            res["verdict"] = "PASS"
        elif res["symbol"] == "XAUUSD":
            # Already live on the DEMO bot — flag rather than auto-remove.
            res["verdict"] = "FLAG (already live; failed OOS bar — consider disabling)"
        else:
            res["verdict"] = "FAIL"

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "recipe": f"{recipe.name} (mode={recipe.mode}, session={recipe.session}, "
        f"min_rr={recipe.min_rr}, filters={recipe.filters})",
        "min_oos_trades": MIN_OOS_TRADES,
        "results": results,
    }

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    Path(args.json_out).write_text(json.dumps(report, indent=2, default=str) + "\n")
    md = render_markdown(report)
    Path(args.md_out).write_text(md)
    print(md)
    print(f"Wrote {args.json_out}")
    print(f"Wrote {args.md_out}")


if __name__ == "__main__":
    main()
