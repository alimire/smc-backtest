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
    strategy_mode: str = "legacy"
    strategy_version: str = "1.1.0"
    gate_reason: str = ""
    parent_amd_id: str = ""
    cisd_bar: Optional[int] = None
    fbos_seen: bool = False


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
    if session == "off":
        return label == "off"
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
    u = symbol.upper().replace("=", "").replace("/", "").strip()
    # DXY only (DX-Y.NYB / DXY / DX=F) — must not match ...USD=X pairs.
    if u.startswith("DX"):
        return 0.05
    # Fusion XAUUSD pipPosition=1 → 0.1
    if "XAU" in u or u.startswith("GOLD"):
        return 0.1
    # JPY-quoted FX (Fusion pipPosition=2 → 0.01)
    if u.endswith("JPY") or u.endswith("JPYX"):
        return 0.01
    # USDX (Fusion dollar index): broker pip is 0.01, but the Jul 2026 OOS
    # study that qualified USDX ran with this FX default (0.0001 → ~zero SL
    # buffer). Keep the scan identical to the backtest; the executor uses the
    # live broker pipPosition for sizing.
    return 0.0001


def sl_buffer(symbol: str, buffer_pips: float = 5.0) -> float:
    return pip_size(symbol) * buffer_pips


# ---------------------------------------------------------------------------
# Data fetch
# ---------------------------------------------------------------------------

def fetch_data(
    symbol: str,
    days: int,
    interval: str = "15m",
    use_cache: bool = True,
    data_source: str = "yahoo",
) -> list[Candle]:
    """Load OHLCV candles. data_source=ctrader uses broker M15 cache when available."""
    from data_fetch import fetch_broker_15m, fetch_ohlcv

    src = (data_source or "yahoo").strip().lower()
    if src == "ctrader" and interval in ("15m", "M15"):
        from data_fetch import _broker_symbol_key

        broker_sym = _broker_symbol_key(symbol)
        fetched = fetch_broker_15m("ctrader", symbol=broker_sym)
        if fetched is None:
            raise RuntimeError(
                f"No cTrader M15 cache for {broker_sym}. Run: "
                f"python scripts/ctrader_fetch_history.py --symbol {broker_sym} --period M15 --days 30"
            )
        df, meta = fetched
        # Optional trim to most recent `days` when caller asks for a shorter window.
        if days and days > 0 and len(df) > 1:
            span_days = max(1, int((df.index[-1] - df.index[0]).total_seconds() // 86400))
            if days < span_days:
                cutoff = df.index[-1] - pd.Timedelta(days=int(days))
                df = df[df.index >= cutoff]
                meta = {**meta, "effective_days": days, "bars": len(df), "trimmed": True}
    else:
        df, meta = fetch_ohlcv(symbol, days, interval, use_cache=use_cache)

    print(
        f"  data: {meta['bars']} bars {meta.get('start')} -> {meta.get('end')} "
        f"(interval={interval}, effective_days={meta.get('effective_days')}, source={meta.get('source', src)})"
    )
    candles = []
    for ts, row in df.iterrows():
        if hasattr(ts, "to_pydatetime"):
            t = ts.to_pydatetime()
        else:
            t = pd.Timestamp(ts).to_pydatetime()
        if getattr(t, "tzinfo", None) is not None:
            t = t.replace(tzinfo=None)
        candles.append(Candle(
            time=t,
            open=float(row["Open"]),
            high=float(row["High"]),
            low=float(row["Low"]),
            close=float(row["Close"]),
            volume=float(row["Volume"]) if "Volume" in row.index else 0.0,
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
    asia_aggressive: bool = False,
) -> tuple[bool, str]:
    """
  Salim: sweep PDH/PDL OR Asia high/low OR session extreme.
  Wick through the level is enough (liquidity grab).
  asia_aggressive: wider Asia buffer + prior-day Asia H/L when available.
    """
    if to_idx <= from_idx:
        return False, ""

    row = ctx[to_idx] if to_idx < len(ctx) else {}
    pdh, pdl = row.get("pdh"), row.get("pdl")
    asia_h, asia_l = row.get("asia_high"), row.get("asia_low")
    # Prior calendar day Asia from any earlier ctx row of previous date
    prev_asia_h = prev_asia_l = None
    if asia_aggressive and to_idx > 0:
        cur_d = _date(candles[to_idx].time)
        for j in range(to_idx - 1, max(-1, to_idx - 120), -1):
            if _date(candles[j].time) != cur_d:
                prev_asia_h = ctx[j].get("asia_high")
                prev_asia_l = ctx[j].get("asia_low")
                break

    asia_buf = pip_size("EURUSD") * (5 if asia_aggressive else 2)
    other_buf = pip_size("EURUSD") * 2

    sources = []
    for i in range(from_idx, to_idx + 1):
        c = candles[i]
        if direction == "bearish":
            levels = []
            if pdh:
                levels.append(("PDH", pdh, other_buf))
            if asia_h:
                levels.append(("AsiaH", asia_h, asia_buf))
            if prev_asia_h:
                levels.append(("PrevAsiaH", prev_asia_h, asia_buf))
            # Recent swing high in lookback
            seg = candles[max(from_idx, i - 12) : i]
            if seg:
                levels.append(("swingH", max(x.high for x in seg), other_buf))
            for name, lvl, buf in levels:
                if c.high >= lvl - buf:
                    sources.append(name)
        else:
            levels = []
            if pdl:
                levels.append(("PDL", pdl, other_buf))
            if asia_l:
                levels.append(("AsiaL", asia_l, asia_buf))
            if prev_asia_l:
                levels.append(("PrevAsiaL", prev_asia_l, asia_buf))
            seg = candles[max(from_idx, i - 12) : i]
            if seg:
                levels.append(("swingL", min(x.low for x in seg), other_buf))
            for name, lvl, buf in levels:
                if c.low <= lvl + buf:
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


def dealing_range(candles: list[Candle], from_idx: int, to_idx: int) -> tuple[float, float]:
    seg = candles[max(0, from_idx) : to_idx + 1]
    if not seg:
        return 0.0, 0.0
    return min(c.low for c in seg), max(c.high for c in seg)


def range_position(price: float, lo: float, hi: float) -> float:
    """0 = range low, 1 = range high."""
    if hi <= lo:
        return 0.5
    return (price - lo) / (hi - lo)


def is_major_idm_zone(
    entry: float,
    idm: Optional[SwingPoint],
    direction: str,
    range_lo: float,
    range_hi: float,
    mid_lo: float = 0.35,
    mid_hi: float = 0.65,
) -> bool:
    """Salim: trade at major IDM — premium for shorts, discount for longs, not mid-range."""
    pos = range_position(entry, range_lo, range_hi)
    if direction == "bearish":
        if pos < mid_hi:
            return False
        if idm and range_position(idm.price, range_lo, range_hi) < 0.5:
            return False
        return True
    if pos > mid_lo:
        return False
    if idm and range_position(idm.price, range_lo, range_hi) > 0.5:
        return False
    return True


def is_choppy(
    candles: list[Candle],
    idx: int,
    window: int = 24,
    efficiency_min: float = 0.22,
    max_pivots: int = 7,
) -> bool:
    """Skip tight alternating price action (no clear orderflow)."""
    start = max(0, idx - window)
    seg = candles[start : idx + 1]
    if len(seg) < 8:
        return False
    net = abs(seg[-1].close - seg[0].open)
    path = sum(abs(seg[i].close - seg[i - 1].close) for i in range(1, len(seg)))
    if path <= 0:
        return True
    efficiency = net / path
    pivots = 0
    for i in range(start + 2, idx - 1):
        if candles[i].high > candles[i - 1].high and candles[i].high > candles[i + 1].high:
            pivots += 1
        if candles[i].low < candles[i - 1].low and candles[i].low < candles[i + 1].low:
            pivots += 1
    return efficiency < efficiency_min or pivots >= max_pivots


def has_real_break_after_liq(
    bos_list: list[dict],
    liq_idx: int,
    entry_idx: int,
    direction: str,
) -> bool:
    """Orderflow shift: BOS in trade direction after liquidity sweep."""
    for bos in bos_list:
        if bos["index"] <= liq_idx or bos["index"] > entry_idx:
            continue
        if bos["direction"] == direction:
            return True
    return False


def smt_level_and_tapped(
    primary: list[Candle],
    correlated: list[Optional[Candle]],
    entry_idx: int,
    direction: str,
    window: int = 12,
    buf: float = 0.00015,
) -> tuple[bool, bool, bool]:
    """
    Salim SMT rule:
    - smt_nearby: divergence visible near entry
    - smt_tapped: correlated pair swept the SMT liquidity level first
    - smt_ready: safe to enter (no pending SMT OR SMT already tapped)
    """
    if entry_idx < window:
        return False, False, True

    corr = correlated[entry_idx] if entry_idx < len(correlated) else None
    if corr is None:
        return False, False, True

    p_win = primary[entry_idx - window : entry_idx]
    c_win = [
        correlated[i] for i in range(entry_idx - window, entry_idx)
        if i < len(correlated) and correlated[i] is not None
    ]
    if len(c_win) < window // 2:
        return False, False, True

    p = primary[entry_idx]
    p_low = min(c.low for c in p_win)
    p_high = max(c.high for c in p_win)
    c_high = max(c.high for c in c_win)
    c_low = min(c.low for c in c_win)

    smt_nearby = False
    smt_level = None
    smt_form_idx = entry_idx

    if direction == "bearish" and p.high > p_high and corr.high <= c_high:
        smt_nearby = True
        smt_level = c_high
        for i in range(entry_idx - window, entry_idx):
            ci = correlated[i]
            if ci and ci.high >= c_high - buf:
                smt_form_idx = i
                break
    elif direction == "bullish" and p.low < p_low and corr.low >= c_low:
        smt_nearby = True
        smt_level = c_low
        for i in range(entry_idx - window, entry_idx):
            ci = correlated[i]
            if ci and ci.low <= c_low + buf:
                smt_form_idx = i
                break

    if not smt_nearby or smt_level is None:
        return False, False, True

    tapped = False
    for i in range(smt_form_idx, entry_idx + 1):
        ci = correlated[i] if i < len(correlated) else None
        if ci is None:
            continue
        if direction == "bearish" and ci.high >= smt_level - buf:
            tapped = True
            break
        if direction == "bullish" and ci.low <= smt_level + buf:
            tapped = True
            break

    smt_ready = tapped
    return smt_nearby, tapped, smt_ready


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
    idm: Optional[SwingPoint] = None,
    tp_mode: str = "nearest_liquidity",
) -> tuple[float, float]:
    """TP at next liquidity pool (or fixed RR); SL beyond protected high/low."""
    row = ctx[entry_idx] if entry_idx < len(ctx) else {}
    mode = (tp_mode or "nearest_liquidity").strip().lower()

    if direction == "bearish":
        if idm is not None:
            protected_high = max(idm.price, max(candles[k].high for k in range(idm.index, entry_idx + 1)))
            sl = protected_high + buf
        else:
            sl = entry + buf * 3
            for i in range(max(0, entry_idx - 8), entry_idx):
                sl = max(sl, candles[i].high + buf)

        if mode == "fixed_rr":
            return sl, entry - (sl - entry) * min_rr

        targets = [row.get("pdl"), row.get("asia_low"), row.get("day_low")]
        seg_low = min(c.low for c in candles[max(0, entry_idx - 20) : entry_idx])
        targets.append(seg_low)
        valid = [t for t in targets if t and t < entry - buf]

        if not valid:
            seg_low_60 = min(c.low for c in candles[max(0, entry_idx - 60) : entry_idx])
            if seg_low_60 < entry - buf:
                valid.append(seg_low_60)

        tp = min(valid) if valid else None
        if tp is None:
            return sl, entry - (sl - entry) * min_rr
        return sl, tp

    # bullish / long
    if idm is not None:
        protected_low = min(idm.price, min(candles[k].low for k in range(idm.index, entry_idx + 1)))
        sl = protected_low - buf
    else:
        sl = entry - buf * 3
        for i in range(max(0, entry_idx - 8), entry_idx):
            sl = min(sl, candles[i].low - buf)

    if mode == "fixed_rr":
        return sl, entry + (entry - sl) * min_rr

    targets = [row.get("pdh"), row.get("asia_high"), row.get("day_high")]
    seg_high = max(c.high for c in candles[max(0, entry_idx - 20) : entry_idx])
    targets.append(seg_high)
    valid = [t for t in targets if t and t > entry + buf]

    if not valid:
        seg_high_60 = max(c.high for c in candles[max(0, entry_idx - 60) : entry_idx])
        if seg_high_60 > entry + buf:
            valid.append(seg_high_60)

    tp = max(valid) if valid else None
    if tp is None:
        return sl, entry + (entry - sl) * min_rr
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
    strategy_mode: str = "advanced",
    advanced_cfg=None,
    return_debug: bool = False,
    candles: Optional[list[Candle]] = None,
    correlated: Optional[list[Optional[Candle]]] = None,
    tp_mode: str = "nearest_liquidity",
    sl_buffer_pips: float = 5.0,
    quiet: bool = False,
    precomputed_timeline=None,
    scan_filters: Optional[dict] = None,
    data_source: str = "yahoo",
) -> list[TradeSetup] | tuple[list[TradeSetup], dict]:
    """
    Scan A+ setups.

    strategy_mode:
      - legacy: mechanical v1.1.0 scanner (unchanged gates)
      - advanced: FBOS≠BOS + CISD/RBOS + Parent AMD lifecycle (default)

    tp_mode: nearest_liquidity | fixed_rr
    candles/correlated: optional preloaded series (skips network fetch)
    precomputed_timeline: optional AdvancedTimeline (skips rebuild; research only)
    scan_filters: optional Frequency-track knobs
      chop_efficiency_min, chop_max_pivots, mid_lo, mid_hi, asia_aggressive
    """
    from advanced_gates import (
        STRATEGY_MODE_ADVANCED,
        STRATEGY_MODE_LEGACY,
        STRATEGY_VERSION,
        AdvancedConfig,
        build_advanced_timeline,
        evaluate_advanced_gates,
        is_fbos_break,
    )
    from data_fetch import bars_for_hours

    mode = (strategy_mode or STRATEGY_MODE_ADVANCED).strip().lower()
    if mode not in (STRATEGY_MODE_LEGACY, STRATEGY_MODE_ADVANCED):
        mode = STRATEGY_MODE_ADVANCED
    cfg = advanced_cfg or AdvancedConfig()
    filters = dict(scan_filters or {})
    chop_efficiency_min = float(filters.get("chop_efficiency_min", 0.22))
    chop_max_pivots = int(filters.get("chop_max_pivots", 7))
    mid_lo = float(filters.get("mid_lo", 0.35))
    mid_hi = float(filters.get("mid_hi", 0.65))
    asia_aggressive = bool(filters.get("asia_aggressive", False))

    if candles is None:
        if not quiet:
            print(f"Fetching {symbol} ({days}d @ {interval}, source={data_source})...")
        candles = fetch_data(symbol, days, interval, data_source=data_source)
    else:
        if not quiet:
            print(f"Using preloaded {len(candles)} candles for {symbol}...")

    is_dxy = "DX" in symbol.upper()
    if correlated is None:
        corr_symbol = "EURUSD=X" if is_dxy else dxy_symbol
        if not quiet:
            print(f"Fetching {corr_symbol} for SMT...")
        try:
            # DXY SMT still Yahoo 15m (~60d); earlier cTrader bars simply lack SMT.
            corr_days = min(days, 60) if interval in ("15m", "M15") else days
            corr_candles = fetch_data(corr_symbol, corr_days, interval, data_source="yahoo")
            correlated = align_correlated(candles, corr_candles)
        except Exception:
            if not quiet:
                print("  Correlated pair fetch failed — SMT skipped")
            correlated = [None] * len(candles)

    ver = STRATEGY_VERSION if mode == STRATEGY_MODE_ADVANCED else "1.1.0"
    if not quiet:
        print(f"Loaded {len(candles)} candles. Scanning Salim v3 ({mode} {ver})...")

    swings = find_swing_points(candles, lookback=3)
    bos_list = detect_bos(candles, swings)
    obs = detect_order_blocks(candles, bos_list)
    fvgs = detect_fvg(candles)
    ctx = build_daily_context(candles)
    buf = sl_buffer(symbol, sl_buffer_pips)

    timeline = None
    if mode == STRATEGY_MODE_ADVANCED:
        if precomputed_timeline is not None:
            timeline = precomputed_timeline
        else:
            timeline = build_advanced_timeline(candles, cfg)

    setups: list[TradeSetup] = []
    seen: set = set()
    reject_counts: dict[str, int] = {}

    def _bump(reason: str) -> None:
        reject_counts[reason] = reject_counts.get(reason, 0) + 1

    lookback = bars_for_hours(interval, 12.0)  # ~12h clock window
    bars_scanned = 0
    bars_in_session = 0

    for i in range(lookback, len(candles)):
        bars_scanned += 1
        if not is_in_session(candles[i].time, session):
            _bump("session_out")
            continue
        bars_in_session += 1

        for direction in ("bearish", "bullish"):
            trade_dir = "short" if direction == "bearish" else "long"
            ob, fvg, poi_type, mid = poi_at_index(obs, fvgs, i, direction)
            if not poi_type:
                _bump("no_poi")
                continue
            if not candle_retests_poi(candles[i], direction, ob, fvg):
                _bump("no_poi_retest")
                continue

            # From here: POI retest candidate — count every reject reason
            _bump("poi_retest_candidate")

            from_idx = max(0, i - lookback)
            liq_ok, liq_src = check_liquidity_sweep(
                candles, ctx, from_idx, i, direction, asia_aggressive=asia_aggressive
            )
            if not liq_ok:
                _bump("no_liquidity_sweep")
                continue

            liq_idx = from_idx
            for j in range(from_idx, i):
                ok, _ = check_liquidity_sweep(
                    candles, ctx, j, j, direction, asia_aggressive=asia_aggressive
                )
                if ok:
                    liq_idx = j

            idm = find_idm_swing(candles, swings, i, direction, liq_idx)
            idm_ok = (
                idm is not None
                and idm.index >= max(0, liq_idx - 8)
                and idm_was_swept(candles, idm, i, direction, buf)
            )
            if not idm_ok:
                _bump("no_idm_sweep")
                continue

            entry = candles[i].close
            if ob:
                entry = (ob.top + ob.bottom) / 2
            elif fvg:
                entry = (fvg.top + fvg.bottom) / 2

            r_lo, r_hi = dealing_range(candles, from_idx, i)
            if not is_major_idm_zone(entry, idm, direction, r_lo, r_hi, mid_lo=mid_lo, mid_hi=mid_hi):
                _bump("mid_range")
                continue

            if is_choppy(
                candles, i, efficiency_min=chop_efficiency_min, max_pivots=chop_max_pivots
            ):
                _bump("chop")
                continue

            if not has_real_break_after_liq(bos_list, liq_idx, i, direction):
                _bump("no_bos_after_liq")
                continue

            # Advanced: mechanical BOS that classified as FBOS does not count as continuation
            if mode == STRATEGY_MODE_ADVANCED and timeline is not None:
                mech_ok = False
                for bos in bos_list:
                    if bos["index"] <= liq_idx or bos["index"] > i:
                        continue
                    if bos["direction"] != direction:
                        continue
                    if is_fbos_break(timeline, bos["index"], direction):
                        continue
                    mech_ok = True
                    break
                # Still allow if CISD/RBOS path will pass evaluate_advanced_gates
                _ = mech_ok

            smt_nearby, smt_tapped, smt_ready = smt_level_and_tapped(
                candles, correlated, i, direction, buf=buf
            )
            if not smt_ready:
                _bump("smt_not_ready")
                continue

            gate_reason = "accept:legacy"
            parent_amd_id = ""
            cisd_bar = None
            fbos_seen = False
            if mode == STRATEGY_MODE_ADVANCED and timeline is not None:
                gate = evaluate_advanced_gates(timeline, i, direction, liq_idx, cfg)
                gate_reason = gate.reason
                parent_amd_id = gate.parent_amd_id or ""
                cisd_bar = gate.cisd_bar
                fbos_seen = gate.fbos_seen
                if not gate.ok:
                    _bump(gate_reason)
                    continue

            sl, tp = find_next_liquidity_tp(
                candles, ctx, i, entry, direction, min_rr, buf, idm, tp_mode=tp_mode
            )
            risk = abs(entry - sl)
            if risk <= 0:
                _bump("zero_risk")
                continue
            rr = abs(tp - entry) / risk
            if rr < min_rr * 0.9:
                _bump("min_rr")
                continue

            smt = smt_nearby and smt_tapped
            sess = get_session_label(candles[i].time)

            key = (_date(candles[i].time), sess, trade_dir, round(entry, 4))
            if key in seen:
                _bump("duplicate")
                continue
            seen.add(key)
            _bump("accept")

            is_aplus = (
                liq_ok and idm_ok and poi_type in ("OB", "FVG", "OB+FVG")
                and smt_ready
            )

            notes = []
            if liq_src:
                notes.append(f"liq:{liq_src}")
            notes.append("IDM swept ✓")
            notes.append("BOS after liq ✓" if mode == STRATEGY_MODE_LEGACY else "RBOS/CISD ✓")
            notes.append("major IDM zone ✓")
            if parent_amd_id:
                notes.append(f"AMD:{parent_amd_id}")
            if smt_nearby:
                notes.append("SMT tapped ✓" if smt_tapped else "SMT wait")
            elif check_smt(candles, correlated, i, direction):
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
                strategy_mode=mode,
                strategy_version=ver,
                gate_reason=gate_reason,
                parent_amd_id=parent_amd_id,
                cisd_bar=cisd_bar,
                fbos_seen=fbos_seen,
            ))

    if return_debug:
        # Candidate-stage rejects (exclude early funnel noise + accept marker)
        early = {"session_out", "no_poi", "no_poi_retest", "poi_retest_candidate", "accept"}
        candidate_rejects = {
            k: v for k, v in reject_counts.items() if k not in early
        }
        debug = {
            "strategy_mode": mode,
            "strategy_version": ver,
            "reject_counts": reject_counts,
            "candidate_reject_counts": candidate_rejects,
            "bars_scanned": bars_scanned,
            "bars_in_session": bars_in_session,
            "scan_filters": {
                "chop_efficiency_min": chop_efficiency_min,
                "chop_max_pivots": chop_max_pivots,
                "mid_lo": mid_lo,
                "mid_hi": mid_hi,
                "asia_aggressive": asia_aggressive,
            },
            "data_source": data_source,
            "candles": candles,
            "fbos_count": len(timeline.fbos_events) if timeline else 0,
            "rbos_count": len(timeline.rbos_events) if timeline else 0,
            "cisd_count": len(timeline.cisd_events) if timeline else 0,
            "amd_count": len(timeline.amds) if timeline else 0,
            "tp_mode": tp_mode,
            "sl_buffer_pips": sl_buffer_pips,
            "entry_model": getattr(cfg, "entry_model", "rbos"),
        }
        return setups, debug
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
    parser.add_argument(
        "--strategy-mode",
        default="advanced",
        choices=["legacy", "advanced"],
        help="legacy=v1.1.0 mechanical; advanced=FBOS/CISD/Parent AMD (default)",
    )
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
        strategy_mode=args.strategy_mode,
    )

    if args.report == "html":
        save_html(setups, f"{args.output}.html")
    elif args.report == "json":
        save_json(setups, f"{args.output}.json")
    else:
        print_report(setups)
