#!/usr/bin/env python3
"""Diagnose which gate rejects the most candidates under the frozen research_best
recipe, per symbol — used to design the A tier (do not change research_best).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

from advanced_gates import get_scan_preset  # noqa: E402
from run_multi_symbol_study import DEFAULT_SYMBOLS, load_symbol_dataset  # noqa: E402
from smc_detector import scan_setups  # noqa: E402

SYMBOLS = tuple(list(DEFAULT_SYMBOLS) + ["USDX"])


def main() -> None:
    recipe = get_scan_preset("research_best")
    totals: dict[str, int] = {}
    out = {}
    for symbol in SYMBOLS:
        dataset = load_symbol_dataset(symbol)
        if dataset is None:
            print(f"[{symbol}] no cache", flush=True)
            continue
        _, debug = scan_setups(
            symbol=f"{symbol}=X",
            days=730,
            interval="15m",
            session=recipe.session,
            min_rr=float(recipe.min_rr),
            strategy_mode=recipe.mode,
            advanced_cfg=recipe.advanced,
            scan_filters=dict(recipe.filters),
            candles=dataset["candles"],
            correlated=dataset["correlated"],
            quiet=True,
            sl_buffer_pips=5.0,
            return_debug=True,
        )
        rejects = debug["candidate_reject_counts"]
        out[symbol] = rejects
        for k, v in rejects.items():
            totals[k] = totals.get(k, 0) + v
        accepts = debug["reject_counts"].get("accept", 0)
        print(f"[{symbol}] accepts={accepts} rejects={rejects}", flush=True)

    print("\n=== TOTAL candidate rejects across symbols ===")
    for k, v in sorted(totals.items(), key=lambda kv: -kv[1]):
        print(f"  {k:30s} {v}")
    (ROOT / "reports" / "tier_reject_diagnosis.json").write_text(
        json.dumps({"per_symbol": out, "totals": totals}, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
