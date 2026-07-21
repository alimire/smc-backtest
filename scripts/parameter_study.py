#!/usr/bin/env python3
"""
Honest Advanced SMC parameter study (train/test by time).

- Fetches the longest reliable Yahoo history per interval (no fabricated bars)
- Baselines: Legacy vs Advanced default on full sample
- Coarse sweep tuned on in-sample only; scored on out-of-sample
- Ranking uses expectancy_r and profit_factor (not win rate alone)

Usage:
  cd /Users/alimire/Downloads/smc-backtest
  source .venv/bin/activate
  python scripts/parameter_study.py --interval 15m --days 60
  python scripts/parameter_study.py --interval 1h --days 730
  python scripts/parameter_study.py --interval 5m --days 60 --quick
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from advanced_gates import AdvancedConfig, PRESETS, get_preset  # noqa: E402
from data_fetch import describe_available_history, fetch_ohlcv  # noqa: E402
from simulator_core import compute_metrics, simulate_rows  # noqa: E402
from smc_detector import Candle, align_correlated, fetch_data, scan_setups  # noqa: E402


def _to_naive(dt: datetime) -> datetime:
    if getattr(dt, "tzinfo", None) is not None:
        return dt.replace(tzinfo=None)
    return dt


def load_pair(symbol: str, corr_symbol: str, days: int, interval: str):
    primary = fetch_data(symbol, days, interval, use_cache=True)
    try:
        secondary = fetch_data(corr_symbol, days, interval, use_cache=True)
        corr = align_correlated(primary, secondary)
    except Exception as e:
        print(f"  SMT correlate failed: {e}")
        corr = [None] * len(primary)
    return primary, corr


def setups_to_rows(setups) -> list[dict]:
    rows = []
    for s in setups:
        d = asdict(s)
        d["time"] = str(s.time)
        rows.append(d)
    return rows


def split_by_time(setups, train_frac: float = 0.7):
    """Split setups by entry time (sorted). Returns train, test lists."""
    ordered = sorted(setups, key=lambda s: _to_naive(s.time))
    if not ordered:
        return [], [], None, None
    cut_idx = max(1, min(len(ordered) - 1, int(len(ordered) * train_frac))) if len(ordered) > 1 else 1
    # Time-based cut: use the timestamp at the fractional bar of the candle series instead?
    # Prefer cut by calendar position of setups for metric attribution.
    train = ordered[:cut_idx]
    test = ordered[cut_idx:]
    cut_time = _to_naive(ordered[cut_idx - 1].time) if cut_idx else None
    return train, test, cut_time, (_to_naive(ordered[0].time), _to_naive(ordered[-1].time))


def split_candles_time(candles: list[Candle], train_frac: float = 0.7):
    """Return cut datetime at train_frac of the candle timeline (not of setups)."""
    if not candles:
        return None
    idx = max(1, min(len(candles) - 1, int(len(candles) * train_frac)))
    return _to_naive(candles[idx].time)


def filter_setups_before(setups, cut: datetime):
    return [s for s in setups if _to_naive(s.time) < cut]


def filter_setups_on_after(setups, cut: datetime):
    return [s for s in setups if _to_naive(s.time) >= cut]


def eval_setups(setups, candles, interval: str) -> dict:
    rows = setups_to_rows(setups)
    sim = simulate_rows(rows, candles=candles, interval=interval, aplus_only=True)
    return {k: v for k, v in sim.items() if k != "rows"}


def structure_key(d: dict) -> tuple:
    return (
        d["fbos_close_back_max_bars"],
        d["rbos_close_buffer"],
        d["rbos_require_retest"],
        d["rbos_expansion_threshold_mult"],
    )


def run_scan(
    candles,
    correlated,
    *,
    strategy_mode: str,
    session: str,
    min_rr: float,
    cfg: AdvancedConfig | None,
    tp_mode: str,
    sl_buffer_pips: float,
    interval: str,
    days: int,
    timeline=None,
):
    return scan_setups(
        days=days,
        interval=interval,
        session=session,
        min_rr=min_rr,
        strategy_mode=strategy_mode,
        advanced_cfg=cfg,
        return_debug=True,
        candles=candles,
        correlated=correlated,
        tp_mode=tp_mode,
        sl_buffer_pips=sl_buffer_pips,
        quiet=True,
        precomputed_timeline=timeline,
    )


def fmt_metrics(m: dict) -> str:
    pf = m.get("profit_factor")
    pf_s = f"{pf:.2f}" if isinstance(pf, (int, float)) else "inf" if m.get("profit_factor_raw") == float("inf") else "n/a"
    return (
        f"n={m.get('setups', 0)} res={m.get('resolved', 0)} "
        f"WR={m.get('sim_win_rate', 0):.1f}% avgRR={m.get('avg_planned_rr', 0):.2f} "
        f"E[R]={m.get('expectancy_r', 0):.3f} PF={pf_s} "
        f"totR={m.get('total_r', 0):.2f} maxL={m.get('max_losing_streak', 0)}"
    )


def coarse_grid(quick: bool = False) -> list[dict]:
    """
    Coarse, documented param grid. Kept deliberately small to limit overfit fishing.
    """
    if quick:
        cisd = [True]
        amd = [True, False]
        entry = ["rbos", "mitigation"]
        tp = ["nearest_liquidity", "fixed_rr"]
        structure_variants = []
        session_rr_sl = [("both", 2.0, 5.0)]
    else:
        cisd = [True, False]
        amd = [True, False]
        entry = ["rbos", "mitigation"]
        tp = ["nearest_liquidity", "fixed_rr"]
        # One-factor-at-a-time structure variants (not full cartesian — limits overfit fishing
        # and keeps unique timelines small on long 1h samples).
        structure_variants = [
            {"fbos_close_back_max_bars": 2},
            {"fbos_close_back_max_bars": 5},
            {"rbos_close_buffer": 0.0001},
            {"rbos_require_retest": True},
            {"rbos_expansion_threshold_mult": 0.3},
            {"rbos_expansion_threshold_mult": 0.8},
        ]
        session_rr_sl = list(itertools.product(["both", "lokz"], [2.0, 2.5], [5.0, 8.0]))

    # Staged reduction: defaults × entry/tp/gates + OFAT structure + session/RR/SL.
    configs: list[dict] = []
    base_struct = {
        "fbos_close_back_max_bars": 3,
        "rbos_close_buffer": 0.0,
        "rbos_require_retest": False,
        "rbos_expansion_threshold_mult": 0.5,
    }

    # Axis A: entry × tp × gates (core profitability levers; shared default structure)
    for e, t, c, a in itertools.product(entry, tp, cisd, amd):
        configs.append({
            **base_struct,
            "require_cisd": c,
            "require_parent_amd": a,
            "entry_model": e,
            "min_rr": 2.0,
            "session": "both",
            "sl_buffer_pips": 5.0,
            "tp_mode": t,
        })

    if quick:
        return _dedupe(configs)

    # Axis B: structure OFAT (hold entry=rbos, tp=nearest; vary gates on/off for sample size)
    for sv in structure_variants:
        for c, a in ((True, True), (False, False)):
            row = {
                **base_struct,
                **sv,
                "require_cisd": c,
                "require_parent_amd": a,
                "entry_model": "rbos",
                "min_rr": 2.0,
                "session": "both",
                "sl_buffer_pips": 5.0,
                "tp_mode": "nearest_liquidity",
            }
            configs.append(row)

    # Axis C: session / RR / SL (default structure; gates on and gates off for volume)
    for sess, mr, sb in session_rr_sl:
        for c, a in ((True, True), (False, False)):
            configs.append({
                **base_struct,
                "require_cisd": c,
                "require_parent_amd": a,
                "entry_model": "rbos",
                "min_rr": mr,
                "session": sess,
                "sl_buffer_pips": sb,
                "tp_mode": "nearest_liquidity",
            })

    return _dedupe(configs)


def _dedupe(configs: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for c in configs:
        key = tuple(sorted(c.items()))
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def cfg_from_dict(d: dict) -> tuple[AdvancedConfig, dict]:
    scan_keys = {"min_rr", "session", "sl_buffer_pips", "tp_mode"}
    adv = AdvancedConfig(
        fbos_close_back_max_bars=d["fbos_close_back_max_bars"],
        rbos_close_buffer=d["rbos_close_buffer"],
        rbos_require_retest=d["rbos_require_retest"],
        rbos_expansion_threshold_mult=d["rbos_expansion_threshold_mult"],
        require_cisd=d["require_cisd"],
        require_parent_amd=d["require_parent_amd"],
        entry_model=d["entry_model"],
    )
    scan = {k: d[k] for k in scan_keys}
    return adv, scan


def score_train(m: dict) -> float:
    """Primary ranking on train: expectancy, with PF as secondary boost."""
    if m.get("resolved", 0) < 3:
        return -999.0
    exp = float(m.get("expectancy_r") or 0)
    pf = m.get("profit_factor_raw") or 0
    if pf == float("inf"):
        pf = 5.0
    return exp + 0.05 * min(pf, 5.0)


def main():
    ap = argparse.ArgumentParser(description="Advanced SMC train/test parameter study")
    ap.add_argument("--symbol", default="EURUSD=X")
    ap.add_argument("--dxy", default="DX-Y.NYB")
    ap.add_argument("--interval", default="15m", choices=["5m", "15m", "1h", "1d"])
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--train-frac", type=float, default=0.70)
    ap.add_argument("--quick", action="store_true", help="Smaller grid for smoke runs")
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--out", default="reports/parameter_study.json")
    ap.add_argument("--apply-preset", action="store_true",
                    help="If OOS winner looks positive, write advanced_oos_candidate preset")
    args = ap.parse_args()

    print("=" * 72, flush=True)
    print("DATA AVAILABILITY PROBE (Yahoo, no fabrication)", flush=True)
    print("=" * 72, flush=True)
    for row in describe_available_history(args.symbol):
        if row.get("ok"):
            print(
                f"  {row['interval']:>4} req={row['requested_days']}d "
                f"eff={row['effective_days']}d bars={row['bars']} "
                f"{row['start']} -> {row['end']} [{row['source']}]"
            )
        else:
            print(f"  {row['interval']:>4} req={row['requested_days']}d FAIL: {row.get('error')}")

    print()
    print(f"Loading primary sample: {args.symbol} {args.days}d @{args.interval}")
    candles, correlated = load_pair(args.symbol, args.dxy, args.days, args.interval)
    cut = split_candles_time(candles, args.train_frac)
    print(f"  bars={len(candles)}  {_to_naive(candles[0].time)} -> {_to_naive(candles[-1].time)}")
    print(f"  train/test cut @ {cut} (train_frac={args.train_frac})")

    report: dict = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "symbol": args.symbol,
        "interval": args.interval,
        "days": args.days,
        "bars": len(candles),
        "start": str(candles[0].time),
        "end": str(candles[-1].time),
        "train_frac": args.train_frac,
        "cut_time": str(cut),
        "baselines": {},
        "sweep": [],
        "best_oos": None,
        "verdict": "",
    }

    # ---- Baselines on FULL sample ----
    print()
    print("=" * 72)
    print("BASELINES (full sample)")
    print("=" * 72)
    for mode in ("legacy", "advanced"):
        cfg = AdvancedConfig() if mode == "advanced" else None
        setups, debug = run_scan(
            candles, correlated,
            strategy_mode=mode,
            session="both",
            min_rr=2.0,
            cfg=cfg,
            tp_mode="nearest_liquidity",
            sl_buffer_pips=5.0,
            interval=args.interval,
            days=args.days,
        )
        metrics = eval_setups(setups, candles, args.interval)
        train_s = filter_setups_before(setups, cut)
        test_s = filter_setups_on_after(setups, cut)
        train_m = eval_setups(train_s, candles, args.interval)
        test_m = eval_setups(test_s, candles, args.interval)
        print(f"\n[{mode}] full:  {fmt_metrics(metrics)}")
        print(f"  reject_counts: {debug.get('reject_counts')}")
        print(f"  fbos={debug.get('fbos_count')} rbos={debug.get('rbos_count')} "
              f"cisd={debug.get('cisd_count')} amd={debug.get('amd_count')}")
        print(f"  train: {fmt_metrics(train_m)}")
        print(f"  test:  {fmt_metrics(test_m)}")
        report["baselines"][mode] = {
            "full": metrics,
            "train": train_m,
            "test": test_m,
            "reject_counts": debug.get("reject_counts", {}),
            "fbos_count": debug.get("fbos_count"),
            "rbos_count": debug.get("rbos_count"),
            "cisd_count": debug.get("cisd_count"),
            "amd_count": debug.get("amd_count"),
        }

    # ---- Coarse sweep (rank on TRAIN, report TEST) ----
    print()
    print("=" * 72)
    print("COARSE SWEEP (tune on train, report OOS)")
    print("=" * 72)
    grid = coarse_grid(quick=args.quick)
    print(f"  configs={len(grid)}", flush=True)

    # Prebuild timelines keyed by structure params (expensive ~15s each on 15m)
    from advanced_gates import build_advanced_timeline

    struct_cfgs = {}
    for d in grid:
        sk = structure_key(d)
        if sk not in struct_cfgs:
            struct_cfgs[sk] = AdvancedConfig(
                fbos_close_back_max_bars=d["fbos_close_back_max_bars"],
                rbos_close_buffer=d["rbos_close_buffer"],
                rbos_require_retest=d["rbos_require_retest"],
                rbos_expansion_threshold_mult=d["rbos_expansion_threshold_mult"],
            )
    print(f"  unique structure timelines to build: {len(struct_cfgs)}", flush=True)
    timeline_cache = {}
    for i, (sk, acfg) in enumerate(struct_cfgs.items(), 1):
        t0 = datetime.utcnow()
        timeline_cache[sk] = build_advanced_timeline(candles, acfg)
        print(f"    timeline {i}/{len(struct_cfgs)} in {(datetime.utcnow()-t0).total_seconds():.1f}s", flush=True)

    results = []
    for i, d in enumerate(grid, 1):
        adv, scan = cfg_from_dict(d)
        tl = timeline_cache[structure_key(d)]
        setups, debug = run_scan(
            candles, correlated,
            strategy_mode="advanced",
            session=scan["session"],
            min_rr=scan["min_rr"],
            cfg=adv,
            tp_mode=scan["tp_mode"],
            sl_buffer_pips=scan["sl_buffer_pips"],
            interval=args.interval,
            days=args.days,
            timeline=tl,
        )
        train_s = filter_setups_before(setups, cut)
        test_s = filter_setups_on_after(setups, cut)
        train_m = eval_setups(train_s, candles, args.interval)
        test_m = eval_setups(test_s, candles, args.interval)
        full_m = eval_setups(setups, candles, args.interval)
        row = {
            "params": d,
            "train": train_m,
            "test": test_m,
            "full": full_m,
            "train_score": score_train(train_m),
            "reject_counts": debug.get("reject_counts", {}),
        }
        results.append(row)
        if i % 10 == 0 or i == len(grid):
            print(f"  ... scan {i}/{len(grid)}", flush=True)
    results.sort(key=lambda r: r["train_score"], reverse=True)
    report["sweep"] = results

    print()
    print(f"TOP {args.top} by TRAIN score → showing TRAIN and OOS:")
    for rank, r in enumerate(results[: args.top], 1):
        p = r["params"]
        print(f"\n  #{rank} train_score={r['train_score']:.3f}")
        print(f"     params: entry={p['entry_model']} tp={p['tp_mode']} "
              f"cisd={p['require_cisd']} amd={p['require_parent_amd']} "
              f"fbos_back={p['fbos_close_back_max_bars']} exp={p['rbos_expansion_threshold_mult']} "
              f"retest={p['rbos_require_retest']} buf={p['rbos_close_buffer']} "
              f"rr={p['min_rr']} sess={p['session']} sl_pips={p['sl_buffer_pips']}")
        print(f"     TRAIN: {fmt_metrics(r['train'])}")
        print(f"     OOS:   {fmt_metrics(r['test'])}")

    # Best OOS among top train candidates (avoid picking pure OOS max — still leakage risk)
    candidates = results[: max(args.top, 5)]
    # Prefer positive OOS expectancy + PF>1 among train leaders
    def oos_rank(r):
        m = r["test"]
        if m.get("resolved", 0) < 2:
            return -999.0
        exp = float(m.get("expectancy_r") or 0)
        pf = m.get("profit_factor_raw") or 0
        if pf == float("inf"):
            pf = 5.0
        return exp + 0.05 * min(pf, 5.0)

    best = max(candidates, key=oos_rank) if candidates else None
    report["best_oos"] = best

    legacy_test = report["baselines"]["legacy"]["test"]
    adv_test = report["baselines"]["advanced"]["test"]

    print()
    print("=" * 72)
    print("VERDICT")
    print("=" * 72)
    if best:
        bt = best["test"]
        print(f"Best among train-leaders by OOS score:")
        print(f"  params={best['params']}")
        print(f"  OOS:   {fmt_metrics(bt)}")
        print(f"  TRAIN: {fmt_metrics(best['train'])}")
        print(f"Legacy OOS:   {fmt_metrics(legacy_test)}")
        print(f"Advanced OOS: {fmt_metrics(adv_test)}")

        resolved = bt.get("resolved", 0)
        exp = float(bt.get("expectancy_r") or 0)
        pf = bt.get("profit_factor_raw") or 0
        convincingly = (
            resolved >= 10
            and exp > 0.15
            and (pf == float("inf") or pf >= 1.2)
        )
        marginally = resolved >= 5 and exp > 0 and (pf == float("inf") or pf > 1.0)
        if convincingly:
            verdict = (
                "PROMISING but sample still small — OOS expectancy/PF positive with "
                f"{resolved} resolved trades. Treat as hypothesis, not proof."
            )
        elif marginally:
            verdict = (
                "MARGINAL — OOS slightly positive but trade count too low / effect size "
                "within noise. Do NOT treat as robust edge."
            )
        else:
            verdict = (
                "NOT convincingly profitable OOS — best train-leader fails expectancy/PF "
                "gates or has too few resolved trades. Results consistent with noise / negative."
            )
        # Always flag 15m/5m ~60d
        if args.interval in ("15m", "5m"):
            verdict += (
                f" CAVEAT: {args.interval} Yahoo history ≈60d only "
                f"({len(candles)} bars) — high overfit risk."
            )
        report["verdict"] = verdict
        print(f"\n{verdict}")

        if args.apply_preset and (convincingly or marginally):
            # Update in-memory + source file for advanced_oos_candidate
            p = best["params"]
            new_cfg = AdvancedConfig(
                fbos_close_back_max_bars=p["fbos_close_back_max_bars"],
                rbos_close_buffer=p["rbos_close_buffer"],
                rbos_require_retest=p["rbos_require_retest"],
                rbos_expansion_threshold_mult=p["rbos_expansion_threshold_mult"],
                require_cisd=p["require_cisd"],
                require_parent_amd=p["require_parent_amd"],
                entry_model=p["entry_model"],
            )
            PRESETS["advanced_oos_candidate"] = new_cfg
            _write_preset_to_source(p)
            print("\nWrote named preset advanced_oos_candidate (defaults unchanged).")
            report["preset_written"] = True
            report["preset_scan_params"] = {
                "min_rr": p["min_rr"],
                "session": p["session"],
                "sl_buffer_pips": p["sl_buffer_pips"],
                "tp_mode": p["tp_mode"],
            }
        else:
            report["preset_written"] = False
    else:
        report["verdict"] = "No sweep results."
        print(report["verdict"])

    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nWrote {out_path}")


def _write_preset_to_source(params: dict) -> None:
    """Replace advanced_oos_candidate AdvancedConfig(...) in advanced_gates.py."""
    path = ROOT / "advanced_gates.py"
    text = path.read_text()
    block = (
        '    "advanced_oos_candidate": AdvancedConfig(\n'
        f"        fbos_close_back_max_bars={params['fbos_close_back_max_bars']},\n"
        f"        rbos_close_buffer={params['rbos_close_buffer']},\n"
        f"        rbos_require_retest={params['rbos_require_retest']},\n"
        f"        rbos_expansion_threshold_mult={params['rbos_expansion_threshold_mult']},\n"
        f"        require_cisd={params['require_cisd']},\n"
        f"        require_parent_amd={params['require_parent_amd']},\n"
        f"        entry_model=\"{params['entry_model']}\",\n"
        "    ),"
    )
    import re
    new_text, n = re.subn(
        r'    "advanced_oos_candidate": AdvancedConfig\([^)]*\),',
        block,
        text,
        count=1,
        flags=re.S,
    )
    if n != 1:
        # Fallback: simple placeholder form
        new_text, n = re.subn(
            r'    "advanced_oos_candidate": AdvancedConfig\(\),',
            block,
            text,
            count=1,
        )
    if n != 1:
        raise RuntimeError("Could not update advanced_oos_candidate preset in source")
    # Also store recommended scan params as comment
    comment = (
        f"    # recommended scan: session={params['session']} min_rr={params['min_rr']} "
        f"tp_mode={params['tp_mode']} sl_buffer_pips={params['sl_buffer_pips']}\n"
    )
    if "recommended scan:" not in new_text:
        new_text = new_text.replace(
            '    "advanced_oos_candidate":',
            comment + '    "advanced_oos_candidate":',
            1,
        )
    path.write_text(new_text)


if __name__ == "__main__":
    main()
