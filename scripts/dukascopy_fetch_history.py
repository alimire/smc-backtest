#!/usr/bin/env python3
"""Fetch EURUSD OHLCV from Dukascopy (free historical feed). No secrets required."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "data_cache"


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Dukascopy EURUSD bars")
    parser.add_argument("--instrument", default="EURUSD")
    parser.add_argument("--interval", default="INTERVAL_MIN_15")
    parser.add_argument("--days", type=int, default=730)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    try:
        from dukascopy_python import INTERVAL_MIN_15, OFFER_SIDE_BID, fetch
    except ImportError:
        print("Install: pip install dukascopy-python")
        sys.exit(1)

    interval = args.interval
    if interval == "INTERVAL_MIN_15":
        interval_const = INTERVAL_MIN_15
    else:
        interval_const = interval

    end = datetime.now(timezone.utc).replace(tzinfo=None)
    start = end - timedelta(days=args.days)
    print(f"Fetching {args.instrument} {args.interval} {start.date()} → {end.date()} ...")

    df = fetch(
        instrument=args.instrument,
        interval=interval_const,
        offer_side=OFFER_SIDE_BID,
        start=start,
        end=end,
        limit=30_000,
    )
    if df is None or df.empty:
        print("No data returned from Dukascopy")
        sys.exit(1)

    # dukascopy returns columns: open, high, low, close, volume (lowercase index)
    out = df.rename(columns={
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume",
    })
    if out.index.tz is None:
        idx = pd.to_datetime(out.index, utc=True)
    else:
        idx = out.index.tz_convert("UTC")
    out.index = idx.tz_convert("Europe/London")
    out.index.name = "Datetime"

    safe = args.instrument.replace("/", "_")
    out_path = Path(args.out) if args.out else CACHE_DIR / f"{safe}_dukascopy_M15_{args.days}d.csv"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path)
    print(f"Saved {len(out)} bars → {out_path}")
    print(f"Range: {out.index[0]} → {out.index[-1]}")


if __name__ == "__main__":
    main()
