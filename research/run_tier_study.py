#!/usr/bin/env python3
"""A-tier walk-forward study (A+ stays frozen research_best; A = ONE relaxation).

Honesty rules:
- A+ = frozen `research_best` (Legacy chop_loose). Its rules are NOT touched;
  the study asserts the A+ subset is identical with the A tier enabled.
- Candidate A relaxations are selected on the TRAIN windows of the same 3-fold
  walk-forward split used by run_multi_symbol_study (train 40/60/80% -> next
  20% test). OOS (union of test windows) is computed only for the
  train-selected winner. No OOS tuning.
- Acceptance bar for DEMO deploy: combined OOS n materially higher than A+
  alone AND A-tier-only OOS E[R] > 0, PF > 1, resolved n >= 15.
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
from run_multi_symbol_study import load_symbol_dataset  # noqa: E402
from run_profitability_study import (  # noqa: E402
    make_folds,
    setups_to_rows,
    subset_rows,
)
from simulator_core import compute_metrics, simulate_rows  # noqa: E402
from smc_detector import scan_setups  # noqa: E402

REPORTS_DIR = ROOT / "reports"
SYMBOLS = (
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

# Candidate A-tier rules: exactly ONE gate softened vs A+ (reject-analysis driven:
# mid_range and no_idm_sweep are the two dominant candidate rejects under
# research_best; chop is already loose in the frozen recipe).
CANDIDATES = {
    "A_mid_40_60": {"a_tier": "mid", "a_mid_lo": 0.40, "a_mid_hi": 0.60},
    "A_mid_42_58": {"a_tier": "mid", "a_mid_lo": 0.42, "a_mid_hi": 0.58},
    "A_mid_45_55": {"a_tier": "mid", "a_mid_lo": 0.45, "a_mid_hi": 0.55},
    "A_idm_flex": {"a_tier": "idm_flex"},
}

MIN_A_OOS_TRADES = 15


def scan_symbol(dataset, recipe, extra_filters=None):
    filters = dict(recipe.filters)
    if extra_filters:
        filters.update(extra_filters)
    return scan_setups(
        symbol=f"{dataset['symbol']}=X",
        days=730,
        interval="15m",
        session=recipe.session,
        min_rr=float(recipe.min_rr),
        strategy_mode=recipe.mode,
        advanced_cfg=recipe.advanced,
        scan_filters=filters,
        candles=dataset["candles"],
        correlated=dataset["correlated"],
        quiet=True,
        sl_buffer_pips=5.0,
    )


def simulate_tier_rows(rows, candle_dicts, tier: str | None):
    """Simulate rows of a given tier ('A+', 'A', or None = both)."""
    targets = [
        dict(r) for r in rows if tier is None or r.get("tier", "A+") == tier
    ]
    if not targets:
        return compute_metrics([]), []
    for r in targets:
        r["is_aplus"] = True  # simulate_rows targets is_aplus rows; tier already filtered
    result = simulate_rows(targets, candles=candle_dicts, interval="15m", aplus_only=True)
    return compute_metrics(result["rows"]), result["rows"]


def key_set(rows):
    return {(r["time"], r["direction"], r["entry_price"], r["stop_loss"]) for r in rows}


def brief(m):
    pf = m.get("profit_factor_raw")
    pf_txt = "inf" if pf == float("inf") else (f"{pf:.2f}" if pf else "0")
    return (
        f"n={m['resolved']} WR={m['sim_win_rate']:.1f}% "
        f"E[R]={m['expectancy_r']:.3f} PF={pf_txt} "
        f"maxLoseStreak={m['max_losing_streak']}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="*", default=list(SYMBOLS))
    parser.add_argument("--json-out", default=str(REPORTS_DIR / "tier_study.json"))
    parser.add_argument("--md-out", default=str(REPORTS_DIR / "tier_study.md"))
    args = parser.parse_args()

    recipe = get_scan_preset("research_best")
    datasets = {}
    baselines = {}
    folds_by_symbol = {}

    for symbol in args.symbols:
        ds = load_symbol_dataset(symbol)
        if ds is None:
            print(f"[{symbol}] no cache — skipped", flush=True)
            continue
        ds["symbol"] = symbol
        datasets[symbol] = ds
        folds_by_symbol[symbol] = make_folds(ds["candle_dicts"])
        base_rows = setups_to_rows(scan_symbol(ds, recipe))
        baselines[symbol] = base_rows
        print(f"[{symbol}] baseline A+ setups={len(base_rows)}", flush=True)

    # --- Candidate scans + A+ invariance check ------------------------------
    candidate_rows = {}
    for cand, extra in CANDIDATES.items():
        candidate_rows[cand] = {}
        for symbol, ds in datasets.items():
            rows = setups_to_rows(scan_symbol(ds, recipe, extra))
            ap_rows = [r for r in rows if r.get("tier") == "A+"]
            if key_set(ap_rows) != key_set(baselines[symbol]):
                raise AssertionError(
                    f"A+ subset changed for {symbol} under {cand} — A tier is not additive"
                )
            candidate_rows[cand][symbol] = rows
        total_a = sum(
            sum(1 for r in rows if r.get("tier") == "A")
            for rows in candidate_rows[cand].values()
        )
        print(f"[{cand}] A+ invariant OK; extra A setups (full sample): {total_a}", flush=True)

    # --- TRAIN selection (union of per-fold train windows, per fold) --------
    train_results = {}
    for cand in CANDIDATES:
        scored_a = []
        for symbol, ds in datasets.items():
            rows = candidate_rows[cand][symbol]
            for fold in folds_by_symbol[symbol]:
                train_rows = subset_rows(rows, start=None, end=fold["train_end_time"])
                a_only = [r for r in train_rows if r.get("tier") == "A"]
                _, sim_rows = simulate_tier_rows(a_only, ds["candle_dicts"], tier=None)
                for r in sim_rows:
                    r["_fold"] = fold["fold"]
                    r["_symbol"] = symbol
                scored_a.extend(sim_rows)
        # Dedupe (nested train windows repeat trades); count each trade once.
        seen = set()
        unique_rows = []
        for r in scored_a:
            k = (r["_symbol"], r["time"], r["direction"], r["entry_price"])
            if k in seen:
                continue
            seen.add(k)
            unique_rows.append(r)
        metrics = compute_metrics(unique_rows)
        train_results[cand] = metrics
        print(f"[train:{cand}] A-only {brief(metrics)}", flush=True)

    def train_ok(m):
        pf = m.get("profit_factor_raw") or 0.0
        return m["resolved"] >= 10 and m["expectancy_r"] > 0 and (
            pf == float("inf") or pf > 1.0
        )

    eligible = {c: m for c, m in train_results.items() if train_ok(m)}
    if not eligible:
        winner = None
    else:
        # Among train-passing candidates prefer the largest n (frequency goal),
        # tie-broken by expectancy.
        winner = max(
            eligible.items(),
            key=lambda kv: (kv[1]["resolved"], kv[1]["expectancy_r"]),
        )[0]
    print(f"\nTrain-selected A-tier rule: {winner}", flush=True)

    # --- OOS evaluation (winner only; union of test windows) ----------------
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "recipe": f"{recipe.name} (mode={recipe.mode}, session={recipe.session}, "
        f"min_rr={recipe.min_rr}, filters={recipe.filters})",
        "candidates": {c: dict(CANDIDATES[c]) for c in CANDIDATES},
        "train_results": {
            c: {k: v for k, v in m.items() if k != "rows"} for c, m in train_results.items()
        },
        "winner": winner,
        "per_symbol": {},
        "combined": {},
        "acceptance": {},
    }

    if winner is not None:
        oos_ap_all, oos_a_all = [], []
        for symbol, ds in datasets.items():
            rows = candidate_rows[winner][symbol]
            oos_rows_sym_ap, oos_rows_sym_a = [], []
            for fold in folds_by_symbol[symbol]:
                test_rows = subset_rows(
                    rows, start=fold["test_start_time"], end=fold["test_end_time"]
                )
                ap = [r for r in test_rows if r.get("tier") == "A+"]
                a = [r for r in test_rows if r.get("tier") == "A"]
                _, ap_sim = simulate_tier_rows(ap, ds["candle_dicts"], tier=None)
                _, a_sim = simulate_tier_rows(a, ds["candle_dicts"], tier=None)
                oos_rows_sym_ap.extend(ap_sim)
                oos_rows_sym_a.extend(a_sim)
            m_ap = compute_metrics(oos_rows_sym_ap)
            m_a = compute_metrics(oos_rows_sym_a)
            m_comb = compute_metrics(oos_rows_sym_ap + oos_rows_sym_a)
            report["per_symbol"][symbol] = {
                "aplus": {k: v for k, v in m_ap.items() if k != "rows"},
                "a": {k: v for k, v in m_a.items() if k != "rows"},
                "combined": {k: v for k, v in m_comb.items() if k != "rows"},
            }
            oos_ap_all.extend(oos_rows_sym_ap)
            oos_a_all.extend(oos_rows_sym_a)
            print(
                f"[OOS:{symbol}] A+ {brief(m_ap)} | A {brief(m_a)} | comb {brief(m_comb)}",
                flush=True,
            )

        m_ap_c = compute_metrics(oos_ap_all)
        m_a_c = compute_metrics(oos_a_all)
        m_comb_c = compute_metrics(oos_ap_all + oos_a_all)
        report["combined"] = {
            "aplus": {k: v for k, v in m_ap_c.items() if k != "rows"},
            "a": {k: v for k, v in m_a_c.items() if k != "rows"},
            "combined": {k: v for k, v in m_comb_c.items() if k != "rows"},
        }
        print(f"\n[OOS combined] A+   {brief(m_ap_c)}")
        print(f"[OOS combined] A    {brief(m_a_c)}")
        print(f"[OOS combined] A+&A {brief(m_comb_c)}")

        pf_a = m_a_c.get("profit_factor_raw") or 0.0
        accept = (
            m_a_c["resolved"] >= MIN_A_OOS_TRADES
            and m_a_c["expectancy_r"] > 0
            and (pf_a == float("inf") or pf_a > 1.0)
            and m_comb_c["resolved"] > m_ap_c["resolved"]
        )
        report["acceptance"] = {
            "a_resolved": m_a_c["resolved"],
            "a_expectancy_r": m_a_c["expectancy_r"],
            "a_profit_factor": m_a_c.get("profit_factor"),
            "combined_resolved": m_comb_c["resolved"],
            "aplus_resolved": m_ap_c["resolved"],
            "n_uplift": round(
                m_comb_c["resolved"] / m_ap_c["resolved"], 3
            ) if m_ap_c["resolved"] else None,
            "passes": accept,
        }
        print(f"\nAcceptance (A: n>={MIN_A_OOS_TRADES}, E[R]>0, PF>1; combined n up): "
              f"{'PASS' if accept else 'FAIL'} — uplift x{report['acceptance']['n_uplift']}")

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    Path(args.json_out).write_text(json.dumps(report, indent=2, default=str) + "\n")

    # Markdown summary
    lines = [
        "## A-tier walk-forward study (A+ frozen; A = one relaxation)",
        "",
        f"Generated {report['generated_at']} — base recipe `{report['recipe']}`.",
        "",
        "### Train selection (A-only rows in train windows; no OOS tuning)",
        "",
        "| Candidate | Rule | Train n | Train E[R] | Train PF |",
        "|---|---|---:|---:|---:|",
    ]
    for cand, m in train_results.items():
        pf = m.get("profit_factor_raw")
        pf_txt = "inf" if pf == float("inf") else (f"{pf:.2f}" if pf else "0")
        mark = " **<- selected**" if cand == winner else ""
        lines.append(
            f"| `{cand}`{mark} | {CANDIDATES[cand]} | {m['resolved']} | "
            f"{m['expectancy_r']:.3f} | {pf_txt} |"
        )
    if winner is not None and report["per_symbol"]:
        lines += [
            "",
            f"### OOS (union of test folds) — winner `{winner}`",
            "",
            "| Symbol | A+ n | A+ E[R] | A+ PF | A n | A E[R] | A PF | Comb n | Comb E[R] | Comb PF |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]

        def cells(m):
            pf = m.get("profit_factor_raw")
            pf_txt = "inf" if pf == float("inf") else (f"{pf:.2f}" if pf else "0")
            return f"{m['resolved']} | {m['expectancy_r']:.3f} | {pf_txt}"

        for symbol, res in report["per_symbol"].items():
            lines.append(
                f"| `{symbol}` | {cells(res['aplus'])} | {cells(res['a'])} | {cells(res['combined'])} |"
            )
        c = report["combined"]
        lines.append(
            f"| **ALL** | {cells(c['aplus'])} | {cells(c['a'])} | {cells(c['combined'])} |"
        )
        acc = report["acceptance"]
        lines += [
            "",
            f"**Acceptance:** {'PASS' if acc['passes'] else 'FAIL'} — "
            f"A-only n={acc['a_resolved']}, E[R]={acc['a_expectancy_r']}, "
            f"PF={acc['a_profit_factor']}; combined n uplift x{acc['n_uplift']}.",
        ]
    lines.append("")
    Path(args.md_out).write_text("\n".join(lines) + "\n")
    print(f"\nWrote {args.json_out}")
    print(f"Wrote {args.md_out}")


if __name__ == "__main__":
    main()
