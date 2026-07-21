"""Frequency-track scan_filters + reject funnel smoke tests."""

from datetime import datetime, timedelta

from smc_detector import Candle, is_choppy, is_major_idm_zone, scan_setups


def _candles(n: int = 80, start: float = 1.10) -> list[Candle]:
    out = []
    t0 = datetime(2025, 1, 6, 8, 0)  # Monday London
    price = start
    for i in range(n):
        o = price
        h = o + 0.0008
        l = o - 0.0008
        c = o + (0.0003 if i % 5 else -0.0002)
        out.append(Candle(time=t0 + timedelta(minutes=15 * i), open=o, high=h, low=l, close=c, volume=100))
        price = c
    return out


def test_is_choppy_respects_efficiency_min():
    candles = _candles(40)
    # zig-zag closes with large path / tiny net → low efficiency
    price = 1.10
    for i, c in enumerate(candles):
        c.open = price
        c.close = price + (-0.002 if i % 2 else 0.002)
        c.high = max(c.open, c.close) + 0.0001
        c.low = min(c.open, c.close) - 0.0001
        price = c.close
    tight = is_choppy(candles, 30, efficiency_min=0.22, max_pivots=99)
    loose = is_choppy(candles, 30, efficiency_min=0.01, max_pivots=99)
    assert tight is True
    assert loose is False


def test_mid_zone_loose_vs_strict():
    # long needs discount: pos <= mid_lo
    assert is_major_idm_zone(1.10, None, "bullish", 1.00, 2.00, mid_lo=0.35, mid_hi=0.65) is True  # pos=0.10
    assert is_major_idm_zone(1.40, None, "bullish", 1.00, 2.00, mid_lo=0.35, mid_hi=0.65) is False  # pos=0.40
    assert is_major_idm_zone(1.40, None, "bullish", 1.00, 2.00, mid_lo=0.42, mid_hi=0.58) is True


def test_scan_setups_return_debug_reject_counts():
    candles = _candles(120)
    setups, debug = scan_setups(
        symbol="EURUSD=X",
        days=1,
        interval="15m",
        session="both",
        min_rr=2.0,
        strategy_mode="legacy",
        candles=candles,
        correlated=[None] * len(candles),
        quiet=True,
        return_debug=True,
        scan_filters={"chop_efficiency_min": 0.15, "chop_max_pivots": 9, "mid_lo": 0.42, "mid_hi": 0.58},
    )
    assert isinstance(setups, list)
    assert "reject_counts" in debug
    assert "candidate_reject_counts" in debug
    assert debug["scan_filters"]["chop_efficiency_min"] == 0.15
