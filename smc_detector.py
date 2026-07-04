"""
SMC (Smart Money Concepts) Detector
Identifies IDM, POI (OB/FVG), BOS, and A+ setups in OHLCV data.

Usage:
    python smc_detector.py --symbol EURUSD=X --days 60 --session NY
    python smc_detector.py --symbol EURUSD=X --days 60 --report html
"""

import argparse
import json
from dataclasses import dataclass, asdict
from datetime import datetime, time
from typing import Optional
import sys

try:
    import yfinance as yf
    import pandas as pd
    import numpy as np
except ImportError:
    print("Install deps: pip install yfinance pandas numpy")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Candle:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open

    @property
    def body_size(self) -> float:
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        return self.high - self.low


@dataclass
class SwingPoint:
    index: int
    time: datetime
    price: float
    kind: str  # 'high' or 'low'


@dataclass
class OrderBlock:
    index: int
    time: datetime
    top: float
    bottom: float
    direction: str  # 'bullish' or 'bearish'
    mitigated: bool = False


@dataclass
class FVG:
    index: int  # middle candle index
    time: datetime
    top: float
    bottom: float
    direction: str  # 'bullish' or 'bearish'
    mitigated: bool = False


@dataclass
class TradeSetup:
    time: datetime
    direction: str          # 'long' or 'short'
    entry_price: float
    stop_loss: float
    take_profit: float
    rr_ratio: float
    pdh_pdl_swept: bool
    idm_swept: bool
    poi_type: str           # 'OB', 'FVG', or 'OB+FVG'
    smt_confluence: bool
    session: str
    is_aplus: bool
    notes: str = ""


# ---------------------------------------------------------------------------
# Core detection functions
# ---------------------------------------------------------------------------

def fetch_data(symbol: str, days: int, interval: str = "15m") -> list[Candle]:
    """Fetch OHLCV data from Yahoo Finance."""
    ticker = yf.Ticker(symbol)
    df = ticker.history(period=f"{days}d", interval=interval)
    if df.empty:
        raise ValueError(f"No data returned for {symbol}")

    candles = []
    for ts, row in df.iterrows():
        candles.append(Candle(
            time=ts.to_pydatetime(),
            open=float(row["Open"]),
            high=float(row["High"]),
            low=float(row["Low"]),
            close=float(row["Close"]),
            volume=float(row["Volume"]),
        ))
    return candles


def find_swing_points(candles: list[Candle], lookback: int = 5) -> list[SwingPoint]:
    """Identify swing highs and lows using a rolling window."""
    swings = []
    for i in range(lookback, len(candles) - lookback):
        window_highs = [c.high for c in candles[i - lookback:i + lookback + 1]]
        window_lows  = [c.low  for c in candles[i - lookback:i + lookback + 1]]

        if candles[i].high == max(window_highs):
            swings.append(SwingPoint(i, candles[i].time, candles[i].high, "high"))
        if candles[i].low == min(window_lows):
            swings.append(SwingPoint(i, candles[i].time, candles[i].low, "low"))
    return swings


def detect_bos(candles: list[Candle], swings: list[SwingPoint]) -> list[dict]:
    """Detect Break of Structure events (close beyond swing point)."""
    bos_events = []
    swing_highs = [s for s in swings if s.kind == "high"]
    swing_lows  = [s for s in swings if s.kind == "low"]

    for i in range(1, len(candles)):
        c = candles[i]
        # Bullish BOS: close above previous swing high
        for sh in swing_highs:
            if sh.index < i and c.close > sh.price:
                bos_events.append({
                    "index": i, "time": c.time, "direction": "bullish",
                    "level": sh.price, "swing_index": sh.index
                })
                swing_highs = [s for s in swing_highs if s.index != sh.index]
                break

        # Bearish BOS: close below previous swing low
        for sl in swing_lows:
            if sl.index < i and c.close < sl.price:
                bos_events.append({
                    "index": i, "time": c.time, "direction": "bearish",
                    "level": sl.price, "swing_index": sl.index
                })
                swing_lows = [s for s in swing_lows if s.index != sl.index]
                break

    return bos_events


def detect_order_blocks(candles: list[Candle], bos_events: list[dict]) -> list[OrderBlock]:
    """Find OBs: last opposing candle before a BOS."""
    obs = []
    for bos in bos_events:
        bos_idx = bos["index"]
        direction = bos["direction"]

        # Look back for last opposing candle
        for j in range(bos_idx - 1, max(0, bos_idx - 20), -1):
            c = candles[j]
            if direction == "bullish" and c.is_bearish:
                obs.append(OrderBlock(
                    index=j, time=c.time,
                    top=max(c.open, c.close),
                    bottom=min(c.open, c.close),
                    direction="bullish"
                ))
                break
            elif direction == "bearish" and c.is_bullish:
                obs.append(OrderBlock(
                    index=j, time=c.time,
                    top=max(c.open, c.close),
                    bottom=min(c.open, c.close),
                    direction="bearish"
                ))
                break
    return obs


def detect_fvg(candles: list[Candle]) -> list[FVG]:
    """Detect Fair Value Gaps (3-candle imbalance)."""
    fvgs = []
    for i in range(1, len(candles) - 1):
        c1, c2, c3 = candles[i - 1], candles[i], candles[i + 1]

        # Bullish FVG: c1.high < c3.low (gap between candle 1 top and candle 3 bottom)
        if c1.high < c3.low:
            fvgs.append(FVG(
                index=i, time=c2.time,
                top=c3.low, bottom=c1.high,
                direction="bullish"
            ))

        # Bearish FVG: c1.low > c3.high
        elif c1.low > c3.high:
            fvgs.append(FVG(
                index=i, time=c2.time,
                top=c1.low, bottom=c3.high,
                direction="bearish"
            ))
    return fvgs


def get_pdh_pdl(candles: list[Candle]) -> list[dict]:
    """Compute Previous Day High/Low for each candle."""
    df = pd.DataFrame([{
        "time": c.time, "high": c.high, "low": c.low,
        "open": c.open, "close": c.close
    } for c in candles])
    df["date"] = pd.to_datetime(df["time"]).dt.date
    daily = df.groupby("date").agg(day_high=("high", "max"), day_low=("low", "min")).reset_index()
    daily["pdh"] = daily["day_high"].shift(1)
    daily["pdl"] = daily["day_low"].shift(1)
    merged = df.merge(daily[["date", "pdh", "pdl"]], on="date", how="left")
    return merged.to_dict("records")


def is_in_session(dt: datetime, session: str) -> bool:
    """
    Check if datetime falls within a trading session.
    yfinance returns UTC times for forex. EST = UTC-4 (EDT) / UTC-5 (EST).
    We use UTC-4 (EDT, most of the trading year).
    NY Open:     13:30–15:00 UTC  (9:30–11:00 EST)
    London Open: 07:00–10:00 UTC  (3:00–6:00 EST)
    """
    # Normalize to UTC hour (strip tzinfo safely)
    t = dt.time() if dt.tzinfo is None else dt.utctimetuple()
    if hasattr(t, 'tm_hour'):
        h, m = t.tm_hour, t.tm_min
    else:
        h, m = t.hour, t.minute

    t_mins = h * 60 + m  # minutes since midnight UTC

    ny_start, ny_end         = 13 * 60 + 30, 15 * 60      # 13:30–15:00 UTC
    london_start, london_end = 7 * 60,        10 * 60      # 07:00–10:00 UTC

    in_ny     = ny_start     <= t_mins <= ny_end
    in_london = london_start <= t_mins <= london_end

    if session == "NY":
        return in_ny
    elif session == "London":
        return in_london
    elif session == "both":
        return in_ny or in_london
    return True  # 'all' — no filter


def detect_idm(candles: list[Candle], swings: list[SwingPoint],
               bos_idx: int, bos_direction: str) -> Optional[SwingPoint]:
    """Find the IDM: the last swing point before the BOS, opposing direction."""
    kind = "low" if bos_direction == "bullish" else "high"
    candidates = [s for s in swings if s.kind == kind and s.index < bos_idx]
    return candidates[-1] if candidates else None


def check_smt(eurusd_candles: list[Candle], dxy_candles: list[Candle],
              idx: int, direction: str, window: int = 10) -> bool:
    """
    Basic SMT check: EURUSD and DXY divergence.
    Bullish SMT: EURUSD lower low but DXY no higher high (or reverse).
    """
    if idx < window or idx >= len(dxy_candles):
        return False

    eu_window = eurusd_candles[idx - window:idx]
    dx_window  = dxy_candles[idx - window:idx] if len(dxy_candles) > idx else []

    if not eu_window or not dx_window:
        return False

    eu_low  = min(c.low  for c in eu_window)
    eu_high = max(c.high for c in eu_window)
    dx_high = max(c.high for c in dx_window)
    dx_low  = min(c.low  for c in dx_window)

    current_eu_low  = eurusd_candles[idx].low
    current_eu_high = eurusd_candles[idx].high
    current_dx_high = dxy_candles[idx].high if idx < len(dxy_candles) else dx_high
    current_dx_low  = dxy_candles[idx].low  if idx < len(dxy_candles) else dx_low

    if direction == "bullish":
        # EURUSD makes lower low but DXY doesn't make higher high
        eu_lower_low = current_eu_low < eu_low
        dx_no_high   = current_dx_high <= dx_high
        return eu_lower_low and dx_no_high

    elif direction == "bearish":
        # EURUSD makes higher high but DXY doesn't make lower low
        eu_higher_high = current_eu_high > eu_high
        dx_no_low      = current_dx_low >= dx_low
        return eu_higher_high and dx_no_low

    return False


# ---------------------------------------------------------------------------
# Main scanner
# ---------------------------------------------------------------------------

def scan_setups(
    symbol: str = "EURUSD=X",
    dxy_symbol: str = "DX-Y.NYB",
    days: int = 60,
    interval: str = "15m",
    session: str = "both",
    min_rr: float = 2.0,
) -> list[TradeSetup]:
    """Full A+ setup scanner."""

    print(f"Fetching {symbol} ({days}d @ {interval})...")
    candles = fetch_data(symbol, days, interval)

    print(f"Fetching DXY for SMT check...")
    try:
        dxy_candles = fetch_data(dxy_symbol, days, interval)
    except Exception:
        print("  DXY fetch failed — SMT will be skipped")
        dxy_candles = []

    print(f"Loaded {len(candles)} candles. Scanning...")

    swings   = find_swing_points(candles, lookback=5)
    bos_list = detect_bos(candles, swings)
    obs      = detect_order_blocks(candles, bos_list)
    fvgs     = detect_fvg(candles)
    pdh_pdl  = get_pdh_pdl(candles)

    setups: list[TradeSetup] = []

    for bos in bos_list:
        bos_idx = bos["index"]
        direction = bos["direction"]  # 'bullish' | 'bearish'
        trade_dir = "long" if direction == "bullish" else "short"

        if bos_idx >= len(candles):
            continue

        candle = candles[bos_idx]
        if not is_in_session(candle.time, session):
            continue

        # --- PDH/PDL sweep check ---
        # Look back up to 20 candles before BOS for the sweep candle
        row = pdh_pdl[bos_idx] if bos_idx < len(pdh_pdl) else {}
        pdh = row.get("pdh")
        pdl = row.get("pdl")
        pdh_pdl_swept = False
        if pdh and pdl:
            # Check current and recent candles for the sweep
            for lookback_i in range(max(0, bos_idx - 20), bos_idx + 1):
                if lookback_i >= len(candles):
                    break
                lc = candles[lookback_i]
                if direction == "bearish" and lc.high > pdh and lc.close < pdh:
                    pdh_pdl_swept = True
                    break
                elif direction == "bullish" and lc.low < pdl and lc.close > pdl:
                    pdh_pdl_swept = True
                    break
            # Also accept: wick above/below PDH/PDL within lookback (even without close back)
            # This catches wicks that swept liquidity before the BOS candle itself
            if not pdh_pdl_swept:
                for lookback_i in range(max(0, bos_idx - 10), bos_idx + 1):
                    if lookback_i >= len(candles):
                        break
                    lc = candles[lookback_i]
                    if direction == "bearish" and lc.high > pdh:
                        pdh_pdl_swept = True
                        break
                    elif direction == "bullish" and lc.low < pdl:
                        pdh_pdl_swept = True
                        break

        # --- IDM sweep check ---
        idm = detect_idm(candles, swings, bos_idx, direction)
        idm_swept = False
        if idm:
            # IDM sweep: any candle AFTER the IDM point and BEFORE/AT the BOS
            # that wicks through the IDM price level (wick beyond it, any close)
            search_start = idm.index + 1
            for c in candles[search_start:bos_idx + 1]:
                if direction == "bullish" and c.low <= idm.price:
                    idm_swept = True
                    break
                elif direction == "bearish" and c.high >= idm.price:
                    idm_swept = True
                    break

        # --- Find matching POI ---
        poi_obs  = [ob for ob in obs if ob.direction == direction
                    and ob.index < bos_idx and not ob.mitigated]
        poi_fvgs = [fvg for fvg in fvgs if fvg.direction == direction
                    and fvg.index < bos_idx and not fvg.mitigated]

        if not poi_obs and not poi_fvgs:
            continue

        # Use most recent POI
        best_ob  = poi_obs[-1]  if poi_obs  else None
        best_fvg = poi_fvgs[-1] if poi_fvgs else None

        # Determine entry zone and POI type
        if best_ob and best_fvg:
            # Overlap = refined POI
            overlap_top = min(best_ob.top, best_fvg.top)
            overlap_bot = max(best_ob.bottom, best_fvg.bottom)
            if overlap_top > overlap_bot:
                entry_price = (overlap_top + overlap_bot) / 2
                poi_type = "OB+FVG"
            else:
                entry_price = (best_ob.top + best_ob.bottom) / 2
                poi_type = "OB"
        elif best_ob:
            entry_price = (best_ob.top + best_ob.bottom) / 2
            poi_type = "OB"
        else:
            entry_price = (best_fvg.top + best_fvg.bottom) / 2
            poi_type = "FVG"

        # --- Stop loss & take profit ---
        if trade_dir == "long":
            sl = best_ob.bottom - 0.0005 if best_ob else entry_price - 0.001
            risk = entry_price - sl
            tp = entry_price + (risk * min_rr)
        else:
            sl = best_ob.top + 0.0005 if best_ob else entry_price + 0.001
            risk = sl - entry_price
            tp = entry_price - (risk * min_rr)

        if risk <= 0:
            continue

        rr = abs(tp - entry_price) / risk

        # --- SMT confluence ---
        smt = check_smt(candles, dxy_candles, bos_idx, direction) if dxy_candles else False

        # --- A+ qualification ---
        is_aplus = pdh_pdl_swept and idm_swept and (poi_type in ("OB", "OB+FVG", "FVG"))

        session_label = "NY" if is_in_session(candle.time, "NY") else "London"

        notes_parts = []
        if not pdh_pdl_swept:
            notes_parts.append("no PDH/PDL sweep")
        if not idm_swept:
            notes_parts.append("IDM not swept")
        if smt:
            notes_parts.append("SMT confluence ✓")

        setups.append(TradeSetup(
            time=candle.time,
            direction=trade_dir,
            entry_price=round(entry_price, 5),
            stop_loss=round(sl, 5),
            take_profit=round(tp, 5),
            rr_ratio=round(rr, 2),
            pdh_pdl_swept=pdh_pdl_swept,
            idm_swept=idm_swept,
            poi_type=poi_type,
            smt_confluence=smt,
            session=session_label,
            is_aplus=is_aplus,
            notes=", ".join(notes_parts),
        ))

    return setups


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_report(setups: list[TradeSetup]) -> None:
    aplus = [s for s in setups if s.is_aplus]
    wins  = [s for s in aplus if s.rr_ratio >= 2.0]  # simplified: RR≥2 = win

    print("\n" + "=" * 60)
    print("SMC BACKTEST REPORT")
    print("=" * 60)
    print(f"Total setups found:      {len(setups)}")
    print(f"A+ setups:               {len(aplus)}")
    print(f"A+ with SMT confluence:  {sum(1 for s in aplus if s.smt_confluence)}")
    print(f"Min RR met (≥2):         {len(wins)} / {len(aplus)}")
    if aplus:
        print(f"Win rate (RR proxy):     {len(wins)/len(aplus)*100:.1f}%")
    print()

    print(f"{'Time':<22} {'Dir':<6} {'Entry':<10} {'SL':<10} {'TP':<10} "
          f"{'RR':<6} {'PDH/L':<6} {'IDM':<5} {'POI':<8} {'SMT':<5} {'A+'}")
    print("-" * 100)
    for s in setups:
        print(
            f"{str(s.time)[:19]:<22} {s.direction:<6} {s.entry_price:<10} "
            f"{s.stop_loss:<10} {s.take_profit:<10} {s.rr_ratio:<6} "
            f"{'✓' if s.pdh_pdl_swept else '✗':<6} "
            f"{'✓' if s.idm_swept else '✗':<5} "
            f"{s.poi_type:<8} "
            f"{'✓' if s.smt_confluence else '✗':<5} "
            f"{'★ A+' if s.is_aplus else ''}"
        )

    print()
    print("A+ SETUPS DETAIL (for expert review):")
    print("-" * 60)
    for s in aplus:
        print(f"\n  {s.time} | {s.direction.upper()} | {s.session} session")
        print(f"  Entry: {s.entry_price}  SL: {s.stop_loss}  TP: {s.take_profit}  RR: {s.rr_ratio}")
        print(f"  POI type: {s.poi_type} | SMT: {'Yes' if s.smt_confluence else 'No'}")
        if s.notes:
            print(f"  Notes: {s.notes}")


def save_json(setups: list[TradeSetup], path: str) -> None:
    data = [asdict(s) | {"time": str(s.time)} for s in setups]
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"JSON saved to {path}")


def save_html(setups: list[TradeSetup], path: str) -> None:
    aplus = [s for s in setups if s.is_aplus]
    rows = ""
    for s in setups:
        cls = "aplus" if s.is_aplus else ""
        rows += f"""
        <tr class="{cls}">
            <td>{str(s.time)[:19]}</td>
            <td>{s.direction}</td>
            <td>{s.entry_price}</td>
            <td>{s.stop_loss}</td>
            <td>{s.take_profit}</td>
            <td>{s.rr_ratio}</td>
            <td>{'✓' if s.pdh_pdl_swept else '✗'}</td>
            <td>{'✓' if s.idm_swept else '✗'}</td>
            <td>{s.poi_type}</td>
            <td>{'✓' if s.smt_confluence else '✗'}</td>
            <td>{s.session}</td>
            <td>{'★' if s.is_aplus else ''}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>SMC Backtest Report</title>
<style>
  body {{ font-family: monospace; background: #0d1117; color: #e6edf3; padding: 20px; }}
  h1 {{ color: #58a6ff; }}
  .summary {{ background: #161b22; padding: 15px; border-radius: 6px; margin-bottom: 20px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 12px; }}
  th {{ background: #21262d; padding: 8px; text-align: left; color: #58a6ff; }}
  td {{ padding: 6px 8px; border-bottom: 1px solid #21262d; }}
  tr.aplus {{ background: #0d2b1a; }}
  tr.aplus td {{ color: #3fb950; }}
</style>
</head><body>
<h1>SMC Backtest Report</h1>
<div class="summary">
  <strong>Total setups:</strong> {len(setups)} &nbsp;|&nbsp;
  <strong>A+ setups:</strong> {len(aplus)} &nbsp;|&nbsp;
  <strong>A+ with SMT:</strong> {sum(1 for s in aplus if s.smt_confluence)}
</div>
<table>
<tr>
  <th>Time</th><th>Dir</th><th>Entry</th><th>SL</th><th>TP</th>
  <th>RR</th><th>PDH/L</th><th>IDM</th><th>POI</th><th>SMT</th>
  <th>Session</th><th>A+</th>
</tr>
{rows}
</table>
</body></html>"""
    with open(path, "w") as f:
        f.write(html)
    print(f"HTML report saved to {path}")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SMC Setup Scanner")
    parser.add_argument("--symbol",   default="EURUSD=X",  help="Yahoo Finance symbol")
    parser.add_argument("--dxy",      default="DX-Y.NYB",  help="DXY symbol for SMT")
    parser.add_argument("--days",     type=int, default=60, help="Lookback days")
    parser.add_argument("--interval", default="15m",        help="Candle interval (15m, 1h)")
    parser.add_argument("--session",  default="both",       help="NY, London, both, all")
    parser.add_argument("--min-rr",   type=float, default=2.0)
    parser.add_argument("--report",   default="console",    help="console, json, html")
    parser.add_argument("--output",   default="smc_report", help="Output filename (no extension)")
    args = parser.parse_args()

    setups = scan_setups(
        symbol=args.symbol,
        dxy_symbol=args.dxy,
        days=args.days,
        interval=args.interval,
        session=args.session,
        min_rr=args.min_rr,
    )

    if args.report == "html":
        save_html(setups, f"{args.output}.html")
    elif args.report == "json":
        save_json(setups, f"{args.output}.json")
    else:
        print_report(setups)
