"""
EURUSD (and related) OHLCV fetch with local CSV cache.

Yahoo Finance hard limits (verified Jul 2026):
  - 15m / 5m: last ~60 calendar days only
  - 1h: ~730 days
  - 1d: multi-year

This module never fabricates bars. Cache only stores what Yahoo returned.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import pandas as pd
    import yfinance as yf
except ImportError as e:
    raise ImportError("pip install yfinance pandas") from e

CACHE_DIR = Path(__file__).resolve().parent / "data_cache"
SESSION_TIMEZONE = "Europe/London"
BROKER_CACHE_GLOB = {
    "ctrader": "{symbol}_ctrader_M15_*.csv",
    "dukascopy": "{symbol}_dukascopy_M15_*.csv",
}

# Conservative caps matching Yahoo's documented/observed limits
INTERVAL_MAX_DAYS = {
    "1m": 7,
    "2m": 60,
    "5m": 60,
    "15m": 60,
    "30m": 60,
    "60m": 730,
    "1h": 730,
    "4h": 730,
    "90m": 60,
    "1d": 3650,
    "1wk": 3650,
    "1mo": 3650,
}


def interval_minutes(interval: str) -> int:
    mapping = {
        "1m": 1,
        "2m": 2,
        "5m": 5,
        "15m": 15,
        "30m": 30,
        "60m": 60,
        "1h": 60,
        "4h": 240,
        "90m": 90,
        "1d": 1440,
    }
    return mapping.get(interval, 15)


def bars_for_hours(interval: str, hours: float = 12.0) -> int:
    """Time-based lookback so 5m/15m/1h share ~same clock window."""
    mins = interval_minutes(interval)
    return max(12, int(hours * 60 / mins))


def clamp_days(days: int, interval: str) -> int:
    cap = INTERVAL_MAX_DAYS.get(interval, 60)
    return max(1, min(int(days), cap))


def _cache_path(symbol: str, days: int, interval: str) -> Path:
    safe = symbol.replace("=", "_").replace("/", "_")
    key = hashlib.md5(f"{symbol}|{days}|{interval}".encode()).hexdigest()[:10]
    return CACHE_DIR / f"{safe}_{interval}_{days}d_{key}.csv"


def _normalize_index_to_london(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize timestamps to Europe/London so session filters are comparable across
    forex, futures, and index symbols.
    """
    if df.empty:
        return df
    out = df.copy()
    idx = out.index
    if not isinstance(idx, pd.DatetimeIndex):
        idx = pd.to_datetime(idx, utc=True, errors="coerce")
    elif idx.tz is None:
        idx = pd.to_datetime(idx, utc=True, errors="coerce")
    else:
        idx = idx.tz_convert("UTC")
    out.index = idx.tz_convert(SESSION_TIMEZONE)
    out = out[~out.index.isna()]
    return out.sort_index()


def fetch_ohlcv(
    symbol: str,
    days: int,
    interval: str = "15m",
    use_cache: bool = True,
    refresh: bool = False,
) -> tuple[pd.DataFrame, dict]:
    """
    Fetch OHLCV. Returns (dataframe, meta).

    meta keys: symbol, requested_days, effective_days, interval, bars,
               start, end, source, cache_path, yahoo_cap
    """
    effective = clamp_days(days, interval)
    path = _cache_path(symbol, effective, interval)
    meta = {
        "symbol": symbol,
        "requested_days": days,
        "effective_days": effective,
        "interval": interval,
        "yahoo_cap": INTERVAL_MAX_DAYS.get(interval, 60),
        "cache_path": str(path),
        "source": "yfinance",
    }

    if use_cache and path.exists() and not refresh:
        df = pd.read_csv(path, parse_dates=["Datetime"], index_col="Datetime")
        df = _normalize_index_to_london(df)
        if not df.empty:
            meta.update({
                "bars": len(df),
                "start": str(df.index[0]),
                "end": str(df.index[-1]),
                "source": "cache",
            })
            return df, meta

    ticker = yf.Ticker(symbol)
    df = ticker.history(period=f"{effective}d", interval=interval)
    if df.empty:
        raise ValueError(
            f"No data for {symbol} period={effective}d interval={interval} "
            f"(Yahoo cap ~{INTERVAL_MAX_DAYS.get(interval, '?')}d)"
        )

    # Normalize columns / index name for CSV round-trip
    out = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    out = _normalize_index_to_london(out)
    out.index.name = "Datetime"

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out.to_csv(path)

    meta.update({
        "bars": len(out),
        "start": str(out.index[0]),
        "end": str(out.index[-1]),
        "source": "yfinance",
    })
    return out, meta


def _broker_symbol_key(symbol: str = "EURUSD") -> str:
    s = symbol.upper().replace("/", "").strip()
    if s.endswith("=X"):
        s = s[:-2]
    return s.replace("=", "").strip() or "EURUSD"


def find_broker_csv(source: str = "ctrader", symbol: str = "EURUSD") -> Path | None:
    """Return newest broker CSV for source+symbol, if any."""
    template = BROKER_CACHE_GLOB.get(source)
    if not template:
        return None
    key = _broker_symbol_key(symbol)
    pattern = template.format(symbol=key)
    matches = sorted(CACHE_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def load_broker_csv(
    path: Path,
    source: str = "ctrader",
    interval: str = "15m",
    symbol: str = "EURUSD",
) -> tuple[pd.DataFrame, dict]:
    """Load OHLCV from a broker-export CSV (cTrader / Dukascopy). Never fabricates bars."""
    df = pd.read_csv(path, parse_dates=["Datetime"], index_col="Datetime")
    df = _normalize_index_to_london(df)
    if df.empty:
        raise ValueError(f"Empty broker CSV: {path}")
    key = _broker_symbol_key(symbol)
    meta = {
        "symbol": f"{key}=X",
        "requested_days": None,
        "effective_days": None,
        "interval": interval,
        "bars": len(df),
        "start": str(df.index[0]),
        "end": str(df.index[-1]),
        "source": source,
        "cache_path": str(path),
    }
    return df, meta


def fetch_broker_15m(
    source: str = "ctrader",
    symbol: str = "EURUSD",
) -> tuple[pd.DataFrame, dict] | None:
    """Load newest cached broker 15m feed for symbol, if present."""
    path = find_broker_csv(source, symbol=symbol)
    if path is None:
        return None
    return load_broker_csv(path, source=source, interval="15m", symbol=symbol)


def df_to_candle_dicts(df: pd.DataFrame) -> list[dict]:
    candles = []
    for ts, row in df.iterrows():
        if hasattr(ts, "to_pydatetime"):
            t = ts.to_pydatetime()
        else:
            t = pd.Timestamp(ts).to_pydatetime()
        if getattr(t, "tzinfo", None) is not None:
            t = t.replace(tzinfo=None)
        candles.append({
            "time": t,
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": float(row["Volume"]) if "Volume" in row else 0.0,
        })
    return candles


def describe_available_history(symbol: str = "EURUSD=X") -> list[dict]:
    """Probe what Yahoo actually returns for common intervals (no fabrication)."""
    probes = [
        ("15m", 60),
        ("15m", 90),
        ("5m", 60),
        ("1h", 730),
        ("1d", 3650),
    ]
    rows = []
    for interval, days in probes:
        try:
            _, meta = fetch_ohlcv(symbol, days, interval, use_cache=True, refresh=False)
            rows.append({**meta, "ok": True, "error": None})
        except Exception as e:
            rows.append({
                "symbol": symbol,
                "interval": interval,
                "requested_days": days,
                "effective_days": clamp_days(days, interval),
                "ok": False,
                "error": str(e),
            })
    return rows
