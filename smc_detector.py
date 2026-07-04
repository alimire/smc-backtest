"""
SMC (Smart Money Concepts) Detector — calibrated to Salim's methodology.

Sessions (UK time): Asia liquidity, lokz (7–10am), ny-am, ny-pm.
A+ sequence: liquidity sweep → IDM sweep → POI retest in kill zone.
"""

import argparse
import json
from dataclasses import dataclass, asdict
from datetime import datetime, time, timedelta
from typing import Optional
import sys

try:
    import yfinance as yf
    import pandas as pd
except ImportError:
    print("Install deps: pip install yfinance pandas")
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


@dataclass
class FVG:
    index: int
    time: datetime
    top: float
    bottom: float
    direction: str


@dataclass
class TradeSetup:
    time: datetime
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    rr_ratio: float
    pdh_pdl_swept: bool
    idm_swept: bool
    poi_type: str
    smt_confluence: bool
    session: str
    is_aplus: bool
    notes: str = ""


# ---------------------------------------------------------------------------
# Time / session helpers (UK local — matches Salim's TradingView)
# ---------------------------------------------------------------------------

def _local_time(dt: datetime) -> time:
    # yfinance forex timestamps are already in the feed's local session time
    return dt.time()


def _date(dt: datetime) -> datetime.date:
    return dt.date()


def _minutes(dt: datetime) -> int:
    t = _local_time(dt)
    return t.hour * 60 + t.minute


def get_session_label(dt: datetime) -> str:
    m = _minutes(dt)
    if 0 <= m < 7 * 60:
        return "Asia"
    if 7 * 60 <= m < 10 * 60:  # lokz: 7am–9:59am UK (includes 9am)
        return "lokz"
    if 14 * 60 + 30 <= m < 16 * 60:
        return "ny-am"
    if 16 * 60 <= m < 19 * 60:
        return "ny-pm"
    return "off"


def is_in_session(dt: datetime, session: str) -> bool:
    label = get_session_label(dt)
    if session in ("all", ""):
        return True
    if session == "lokz":
        return label == "lokz"
    if session == "ny-am":
        return label == "ny-am"
    if session == "ny-pm":
        return label == "ny-pm"
    if session == "asia":
        return label == "Asia"
    if session == "London":
        return label == "lokz"
    if session == "NY":
        return label in ("ny-am", "ny-pm")
    # Salim default: lokz + ny-am kill zones
    if session in ("both", "killzones"):
        return label in ("lokz", "ny-am")
    return True


def pip_size(symbol: str) -> float:
    if "DX" in symbol.upper() or symbol.upper().startswith("DX-"):
        return 0.05
    return 0.0001


def sl_buffer(symbol: str) -> float:
    return pip_size(symbol) * 5


# ---------------------------------------------------------------------------
# Data fetch
# ---------------------------------------------------------------------------

def fetch_data(symbol: str, days: int, interval: str = "15m") -> list[Candle]:
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


def align_correlated(
    primary: list[Candle], secondary: list[Candle]
) -> list[Optional[Candle]]:
    """Map each primary candle to the nearest secondary candle by time."""
    if not secondary:
        return [None] * len(primary)
    sec_times = [c.time for c in secondary]
    aligned: list[Optional[Candle]] = []
    j = 0
    for pc in primary:
        while j + 1 < len(secondary) and sec_times[j + 1] <= pc.time:
            j += 1
        if sec_times[j] == pc.time or (
            j + 1 < len(secondary)
            and abs((sec_times[j] - pc.time).total_seconds())
            <= abs((sec_times[j + 1] - pc.time).total_seconds())
        ):
            aligned.append(secondary[j])
        else:
            aligned.append(secondary[min(j, len(secondary) - 1)])
    return aligned


# ---------------------------------------------------------------------------
# Structure detection
# ---------------------------------------------------------------------------

def find_swing_points(candles: list[Candle], lookback: int = 3) -> list[SwingPoint]:
    swings = []
    for i in range(lookback, len(candles) - lookback):
        wh = [c.high for c in candles[i - lookback : i + lookback + 1]]
        wl = [c.low for c in candles[i - lookback : i + lookback + 1]]
        if candles[i].high == max(wh):
            swings.append(SwingPoint(i, candles[i].time, candles[i].high, "high"))
        if candles[i].low == min(wl):
            swings.append(SwingPoint(i, candles[i].time, candles[i].low, "low"))
    return swings


def detect_bos(candles: list[Candle], swings: list[SwingPoint]) -> list[dict]:
    bos_events = []
    swing_highs = [s for s in swings if s.kind == "high"]
    swing_lows = [s for s in swings if s.kind == "low"]

    for i in range(1, len(candles)):
        c = candles[i]
        for sh in swing_highs:
            if sh.index < i and c.close > sh.price:
                bos_events.append({
                    "index": i, "time": c.time, "direction": "bullish",
                    "level": sh.price, "swing_index": sh.index,
                })
                swing_highs = [s for s in swing_highs if s.index != sh.index]
                break
        for sl in swing_lows:
            if sl.index < i and c.close < sl.price:
                bos_events.append({
                    "index": i, "time": c.time, "direction": "bearish",
                    "level": sl.price, "swing_index": sl.index,
                })
                swing_lows = [s for s in swing_lows if s.index != sl.index]
                break
    return bos_events


def detect_order_blocks(candles: list[Candle], bos_events: list[dict]) -> list[OrderBlock]:
    obs = []
    for bos in bos_events:
        bos_idx = bos["index"]
        direction = bos["direction"]
        for j in range(bos_idx - 1, max(0, bos_idx - 25), -1):
            c = candles[j]
            if direction == "bullish" and c.is_bearish:
                obs.append(OrderBlock(
                    index=j, time=c.time,
                    top=max(c.open, c.close), bottom=min(c.open, c.close),
                    direction="bullish",
                ))
                break
            if direction == "bearish" and c.is_bullish:
                obs.append(OrderBlock(
                    index=j, time=c.time,
                    top=max(c.open, c.close), bottom=min(c.open, c.close),
                    direction="bearish",
                ))
                break
    return obs


def detect_fvg(candles: list[Candle]) -> list[FVG]:
    fvgs = []
    for i in range(1, len(candles) - 1):
        c1, c2, c3 = candles[i - 1], candles[i], candles[i + 1]
        if c1.high < c3.low:
            fvgs.append(FVG(index=i, time=c2.time, top=c3.low, bottom=c1.high, direction="bullish"))
        elif c1.low > c3.high:
            fvgs.append(FVG(index=i, time=c2.time, top=c1.low, bottom=c3.high, direction="bearish"))
    return fvgs


# ---------------------------------------------------------------------------
# Salim liquidity levels
# ---------------------------------------------------------------------------

def build_daily_context(candles: list[Candle]) -> list[dict]:
    """Per-candle context: PDH/PDL, Asia range, lokz opening range."""
    rows = []
    by_date: dict = {}

    for i, c in enumerate(candles):
        d = _date(c.time)
        m = _minutes(c.time)
        if d not in by_date:
            by_date[d] = {
                "highs": [], "lows": [],
                "asia_high": None, "asia_low": None,
                "day_high": None, "day_low": None,
            }
        day = by_date[d]
        day["highs"].append(c.high)
        day["lows"].append(c.low)
        day["day_high"] = max(day["highs"])
        day["day_low"] = min(day["lows"])
        if m < 7 * 60:
            day["asia_high"] = c.high if day["asia_high"] is None else max(day["asia_high"], c.high)
            day["asia_low"] = c.low if day["asia_low"] is None else min(day["asia_low"], c.low)

    dates = sorted(by_date.keys())
    pdh_map, pdl_map = {}, {}
    for idx, d in enumerate(dates):
        if idx > 0:
            prev = by_date[dates[idx - 1]]
            pdh_map[d] = prev["day_high"]
            pdl_map[d] = prev["day_low"]

    for i, c in enumerate(candles):
        d = _date(c.time)
        day = by_date[d]
        rows.append({
            "index": i,
            "pdh": pdh_map.get(d),
            "pdl": pdl_map.get(d),
            "asia_high": day["asia_high"],
            "asia_low": day["asia_low"],
            "day_high": day["day_high"],
            "day_low": day["day_low"],
        })
    return rows


def check_liquidity_sweep(
    candles: list[Candle],
    ctx: list[dict],
    from_idx: int,
    to_idx: int,
    direction: str,
) -> tuple[bool, str]:
    """
  Salim: sweep PDH/PDL OR Asia high/low OR session extreme.
  Wick through the level is enough (liquidity grab).
    """
    if to_idx <= from_idx:
        return False, ""

    row = ctx[to_idx] if to_idx < len(ctx) else {}
    pdh, pdl = row.get("pdh"), row.get("pdl")
    asia_h, asia_l = row.get("asia_high"), row.get("asia_low")

    sources = []
    for i in range(from_idx, to_idx + 1):
        c = candles[i]
        if direction == "bearish":
            levels = []
            if pdh:
                levels.append(("PDH", pdh))
            if asia_h:
                levels.append(("AsiaH", asia_h))
            # Recent swing high in lookback
            seg = candles[max(from_idx, i - 12) : i]
            if seg:
                levels.append(("swingH", max(x.high for x in seg)))
            for name, lvl in levels:
                if c.high >= lvl - pip_size("EURUSD") * 2:
                    sources.append(name)
        else:
            levels = []
            if pdl:
                levels.append(("PDL", pdl))
            if asia_l:
                levels.append(("AsiaL", asia_l))
            seg = candles[max(from_idx, i - 12) : i]
            if seg:
                levels.append(("swingL", min(x.low for x in seg)))
            for name, lvl in levels:
                if c.low <= lvl + pip_size("EURUSD") * 2:
                    sources.append(name)

    if sources:
        return True, "+".join(sorted(set(sources)))
    return False, ""


def find_idm_swing(
    candles: list[Candle],
    swings: list[SwingPoint],
    poi_idx: int,
    direction: str,
    search_from: int,
) -> Optional[SwingPoint]:
    """Salim's IDM: minor inducement before POI; fall back to local extreme."""
    kind = "high" if direction == "bearish" else "low"
    candidates = [
        s for s in swings
        if s.kind == kind and search_from <= s.index < poi_idx
    ]
    if candidates:
        return candidates[-1]
    # Synthetic IDM: local extreme in the 12 candles before entry
    start = max(search_from, poi_idx - 12)
    window = candles[start:poi_idx]
    if not window:
        return None
    if direction == "bearish":
        best = max(range(len(window)), key=lambda k: window[k].high)
        ci = start + best
        return SwingPoint(ci, candles[ci].time, candles[ci].high, "high")
    best = min(range(len(window)), key=lambda k: window[k].low)
    ci = start + best
    return SwingPoint(ci, candles[ci].time, candles[ci].low, "low")


def idm_was_swept(
    candles: list[Candle],
    idm: SwingPoint,
    entry_idx: int,
    direction: str,
    buf: float = 0.00015,
) -> bool:
    """Wick through IDM after it forms (Salim: liquidity grab)."""
    for i in range(idm.index + 1, entry_idx + 1):
        c = candles[i]
        if direction == "bearish" and c.high >= idm.price - buf:
            return True
        if direction == "bullish" and c.low <= idm.price + buf:
            return True
    return False


def check_smt(
    primary: list[Candle],
    correlated: list[Optional[Candle]],
    idx: int,
    direction: str,
    window: int = 8,
) -> bool:
    """EURUSD vs DXY divergence at entry (or DXY vs EURUSD when scanning DXY)."""
    if idx < window or idx >= len(primary):
        return False
    corr = correlated[idx] if idx < len(correlated) else None
    if corr is None:
        return False

    p_win = primary[idx - window : idx]
    c_win = [
        correlated[i] for i in range(idx - window, idx)
        if i < len(correlated) and correlated[i] is not None
    ]
    if len(c_win) < window // 2:
        return False

    p = primary[idx]
    p_low = min(c.low for c in p_win)
    p_high = max(c.high for c in p_win)
    c_high = max(c.high for c in c_win)
    c_low = min(c.low for c in c_win)

    if direction == "bullish":
        return p.low < p_low and corr.high <= c_high
    if direction == "bearish":
        return p.high > p_high and corr.low >= c_low
    return False


def poi_at_index(
    obs: list[OrderBlock],
    fvgs: list[FVG],
    idx: int,
    direction: str,
) -> tuple[Optional[OrderBlock], Optional[FVG], str, float]:
    d = direction
    matching_obs = [ob for ob in obs if ob.direction == d and ob.index <= idx]
    matching_fvgs = [f for f in fvgs if f.direction == d and f.index <= idx]
    best_ob = matching_obs[-1] if matching_obs else None
    best_fvg = matching_fvgs[-1] if matching_fvgs else None

    if best_ob and best_fvg:
        overlap_top = min(best_ob.top, best_fvg.top)
        overlap_bot = max(best_ob.bottom, best_fvg.bottom)
        if overlap_top > overlap_bot:
            return best_ob, best_fvg, "OB+FVG", (overlap_top + overlap_bot) / 2
        return best_ob, best_fvg, "OB", (best_ob.top + best_ob.bottom) / 2
    if best_ob:
        return best_ob, None, "OB", (best_ob.top + best_ob.bottom) / 2
    if best_fvg:
        return None, best_fvg, "FVG", (best_fvg.top + best_fvg.bottom) / 2
    return None, None, "", 0.0


def candle_retests_poi(c: Candle, direction: str, ob: Optional[OrderBlock], fvg: Optional[FVG]) -> bool:
    if direction == "bearish":
        top = ob.top if ob else (fvg.top if fvg else 0)
        bot = ob.bottom if ob else (fvg.bottom if fvg else 0)
        return c.high >= bot and c.close <= top + (top - bot) * 0.5
    top = ob.top if ob else (fvg.top if fvg else 0)
    bot = ob.bottom if ob else (fvg.bottom if fvg else 0)
    return c.low <= top and c.close >= bot - (top - bot) * 0.5


def find_next_liquidity_tp(
    candles: list[Candle],
    ctx: list[dict],
    entry_idx: int,
    entry: float,
    direction: str,
    min_rr: float,
    buf: float,
) -> tuple[float, float]:
    """TP at next liquidity pool; SL beyond POI wick."""
    row = ctx[entry_idx] if entry_idx < len(ctx) else {}
    if direction == "bearish":
        sl = entry + buf * 3
        for i in range(max(0, entry_idx - 8), entry_idx):
            sl = max(sl, candles[i].high + buf)
        targets = [row.get("pdl"), row.get("asia_low"), row.get("day_low")]
        seg_low = min(c.low for c in candles[max(0, entry_idx - 20) : entry_idx])
        targets.append(seg_low)
        valid = [t for t in targets if t and t < entry - buf]
        tp = min(valid) if valid else entry - (sl - entry) * min_rr
        risk = sl - entry
        if risk > 0 and (entry - tp) / risk < min_rr:
            tp = entry - risk * min_rr
        return sl, tp
    sl = entry - buf * 3
    for i in range(max(0, entry_idx - 8), entry_idx):
        sl = min(sl, candles[i].low - buf)
    targets = [row.get("pdh"), row.get("asia_high"), row.get("day_high")]
    seg_high = max(c.high for c in candles[max(0, entry_idx - 20) : entry_idx])
    targets.append(seg_high)
    valid = [t for t in targets if t and t > entry + buf]
    tp = max(valid) if valid else entry + (entry - sl) * min_rr
    risk = entry - sl
    if risk > 0 and (tp - entry) / risk < min_rr:
        tp = entry + risk * min_rr
    return sl, tp


# ---------------------------------------------------------------------------
# Main scanner — POI retest driven (Salim model)
# ---------------------------------------------------------------------------

def scan_setups(
    symbol: str = "EURUSD=X",
    dxy_symbol: str = "DX-Y.NYB",
    days: int = 60,
    interval: str = "15m",
    session: str = "both",
    min_rr: float = 2.0,
) -> list[TradeSetup]:
    print(f"Fetching {symbol} ({days}d @ {interval})...")
    candles = fetch_data(symbol, days, interval)

    is_dxy = "DX" in symbol.upper()
    corr_symbol = "EURUSD=X" if is_dxy else dxy_symbol
    print(f"Fetching {corr_symbol} for SMT...")
    try:
        corr_candles = fetch_data(corr_symbol, days, interval)
        correlated = align_correlated(candles, corr_candles)
    except Exception:
        print("  Correlated pair fetch failed — SMT skipped")
        correlated = [None] * len(candles)

    print(f"Loaded {len(candles)} candles. Scanning (Salim rules)...")

    swings = find_swing_points(candles, lookback=3)
    bos_list = detect_bos(candles, swings)
    obs = detect_order_blocks(candles, bos_list)
    fvgs = detect_fvg(candles)
    ctx = build_daily_context(candles)
    buf = sl_buffer(symbol)

    setups: list[TradeSetup] = []
    seen: set = set()

    lookback = 48  # ~12h on 15m

    for i in range(lookback, len(candles)):
        if not is_in_session(candles[i].time, session):
            continue

        for direction in ("bearish", "bullish"):
            trade_dir = "short" if direction == "bearish" else "long"
            ob, fvg, poi_type, mid = poi_at_index(obs, fvgs, i, direction)
            if not poi_type:
                continue
            if not candle_retests_poi(candles[i], direction, ob, fvg):
                continue

            from_idx = max(0, i - lookback)
            liq_ok, liq_src = check_liquidity_sweep(candles, ctx, from_idx, i, direction)
            if not liq_ok:
                continue

            liq_idx = from_idx
            for j in range(from_idx, i):
                ok, _ = check_liquidity_sweep(candles, ctx, j, j, direction)
                if ok:
                    liq_idx = j

            idm = find_idm_swing(candles, swings, i, direction, liq_idx)
            idm_ok = (
                idm is not None
                and idm.index >= max(0, liq_idx - 8)
                and idm_was_swept(candles, idm, i, direction, buf)
            )

            entry = candles[i].close
            if ob:
                entry = (ob.top + ob.bottom) / 2
            elif fvg:
                entry = (fvg.top + fvg.bottom) / 2

            sl, tp = find_next_liquidity_tp(candles, ctx, i, entry, direction, min_rr, buf)
            risk = abs(entry - sl)
            if risk <= 0:
                continue
            rr = abs(tp - entry) / risk
            if rr < min_rr * 0.9:
                continue

            smt = check_smt(candles, correlated, i, direction)
            sess = get_session_label(candles[i].time)

            key = (_date(candles[i].time), sess, trade_dir, round(entry, 4))
            if key in seen:
                continue
            seen.add(key)

            is_aplus = liq_ok and idm_ok and poi_type in ("OB", "FVG", "OB+FVG")

            notes = []
            if liq_src:
                notes.append(f"liq:{liq_src}")
            if idm_ok:
                notes.append("IDM swept ✓")
            elif idm:
                notes.append("IDM present, sweep weak")
            else:
                notes.append("no IDM")
            if smt:
                notes.append("SMT ✓")

            setups.append(TradeSetup(
                time=candles[i].time,
                direction=trade_dir,
                entry_price=round(entry, 5 if not is_dxy else 3),
                stop_loss=round(sl, 5 if not is_dxy else 3),
                take_profit=round(tp, 5 if not is_dxy else 3),
                rr_ratio=round(rr, 2),
                pdh_pdl_swept=liq_ok,
                idm_swept=idm_ok,
                poi_type=poi_type,
                smt_confluence=smt,
                session=sess,
                is_aplus=is_aplus,
                notes=", ".join(notes),
            ))

    return setups


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_report(setups: list[TradeSetup]) -> None:
    aplus = [s for s in setups if s.is_aplus]
    wins = [s for s in aplus if s.rr_ratio >= 2.0]

    print("\n" + "=" * 60)
    print("SMC BACKTEST REPORT (Salim rules)")
    print("=" * 60)
    print(f"Total setups found:      {len(setups)}")
    print(f"A+ setups:               {len(aplus)}")
    print(f"A+ with SMT confluence:  {sum(1 for s in aplus if s.smt_confluence)}")
    print(f"Min RR met (≥2):         {len(wins)} / {len(aplus)}")
    if aplus:
        print(f"Win rate (RR proxy):     {len(wins)/len(aplus)*100:.1f}%")
    print()

    print(f"{'Time':<22} {'Dir':<6} {'Entry':<10} {'SL':<10} {'TP':<10} "
          f"{'RR':<6} {'Liq':<6} {'IDM':<5} {'POI':<8} {'SMT':<5} {'Sess':<6} {'A+'}")
    print("-" * 110)
    for s in setups:
        print(
            f"{str(s.time)[:19]:<22} {s.direction:<6} {s.entry_price:<10} "
            f"{s.stop_loss:<10} {s.take_profit:<10} {s.rr_ratio:<6} "
            f"{'✓' if s.pdh_pdl_swept else '✗':<6} "
            f"{'✓' if s.idm_swept else '✗':<5} "
            f"{s.poi_type:<8} "
            f"{'✓' if s.smt_confluence else '✗':<5} "
            f"{s.session:<6} "
            f"{'★ A+' if s.is_aplus else ''}"
        )


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
            <td>{str(s.time)[:19]}</td><td>{s.direction}</td>
            <td>{s.entry_price}</td><td>{s.stop_loss}</td><td>{s.take_profit}</td>
            <td>{s.rr_ratio}</td>
            <td>{'✓' if s.pdh_pdl_swept else '✗'}</td>
            <td>{'✓' if s.idm_swept else '✗'}</td>
            <td>{s.poi_type}</td>
            <td>{'✓' if s.smt_confluence else '✗'}</td>
            <td>{s.session}</td><td>{'★' if s.is_aplus else ''}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>SMC Backtest</title>
<style>
  body {{ font-family: monospace; background: #0d1117; color: #e6edf3; padding: 20px; }}
  h1 {{ color: #58a6ff; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 12px; }}
  th {{ background: #21262d; padding: 8px; color: #58a6ff; }}
  td {{ padding: 6px 8px; border-bottom: 1px solid #21262d; }}
  tr.aplus {{ background: #0d2b1a; }}
</style></head><body>
<h1>SMC Backtest (Salim rules)</h1>
<p>Total: {len(setups)} | A+: {len(aplus)} | SMT: {sum(1 for s in aplus if s.smt_confluence)}</p>
<table><tr><th>Time</th><th>Dir</th><th>Entry</th><th>SL</th><th>TP</th>
<th>RR</th><th>Liq</th><th>IDM</th><th>POI</th><th>SMT</th><th>Session</th><th>A+</th></tr>
{rows}</table></body></html>"""
    with open(path, "w") as f:
        f.write(html)
    print(f"HTML report saved to {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SMC Setup Scanner (Salim rules)")
    parser.add_argument("--symbol", default="EURUSD=X")
    parser.add_argument("--dxy", default="DX-Y.NYB")
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--interval", default="15m")
    parser.add_argument("--session", default="both", help="both/killzones, lokz, ny-am, ny-pm, all")
    parser.add_argument("--min-rr", type=float, default=2.0)
    parser.add_argument("--report", default="console")
    parser.add_argument("--output", default="smc_report")
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
