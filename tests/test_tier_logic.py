"""A-tier (single relaxation) + correlation guard tests.

A+ must stay unchanged: enabling the A tier may only ADD rows labelled tier="A".
"""

from pathlib import Path

import pytest

from executor_core import (
    AccountSnapshot,
    ExecutionPolicy,
    TradeIntent,
    default_instrument,
    evaluate_trade,
)

ROOT = Path(__file__).resolve().parents[1]
EURUSD_CACHE = ROOT / "data_cache" / "EURUSD_ctrader_M15_730d.csv"


def _snapshot(**overrides):
    base = dict(
        host="demo",
        ctid_account_id=47_820_966,
        trader_login=10_123_191,
        balance=10_000.0,
        open_positions=0,
        daily_realized_pnl=0.0,
    )
    base.update(overrides)
    return AccountSnapshot(**base)


def _intent(**overrides):
    base = dict(
        setup_id="tier-001",
        symbol="EURUSD",
        side="long",
        entry=1.1000,
        stop_loss=1.0990,
        take_profit=1.1020,
    )
    base.update(overrides)
    return TradeIntent(**base)


class TestTierPolicy:
    def test_a_tier_allowed_and_forced_to_min_volume(self):
        intent = _intent(is_aplus=False, tier="A")
        decision = evaluate_trade(
            intent, _snapshot(), instrument=default_instrument("EURUSD")
        )
        assert decision.allowed, decision.reasons
        assert decision.broker_volume_cents == default_instrument("EURUSD").min_volume_cents

    def test_aplus_tier_still_requires_aplus_flag(self):
        intent = _intent(is_aplus=False, tier="A+")
        reasons = evaluate_trade(intent, _snapshot()).reasons
        assert "only A+ setups are allowed" in reasons

    def test_unknown_tier_blocked(self):
        policy = ExecutionPolicy(allowed_tiers=frozenset({"A+"}))
        intent = _intent(is_aplus=False, tier="A")
        reasons = evaluate_trade(intent, _snapshot(), policy=policy).reasons
        assert any("tier A not allowed" in r for r in reasons)

    def test_aplus_default_tier_unchanged(self):
        decision = evaluate_trade(_intent(), _snapshot())
        assert decision.allowed, decision.reasons


class TestCorrelationGuard:
    def test_blocks_at_max_total_open_positions(self):
        snapshot = _snapshot(open_positions=3, open_positions_for_symbol=0)
        reasons = evaluate_trade(_intent(), snapshot).reasons
        assert any("correlation guard" in r for r in reasons)

    def test_allows_below_cap(self):
        snapshot = _snapshot(open_positions=2, open_positions_for_symbol=0)
        decision = evaluate_trade(_intent(), snapshot)
        assert decision.allowed, decision.reasons

    def test_applies_to_a_tier_too(self):
        snapshot = _snapshot(open_positions=5, open_positions_for_symbol=0)
        intent = _intent(is_aplus=False, tier="A")
        reasons = evaluate_trade(
            intent, snapshot, instrument=default_instrument("EURUSD")
        ).reasons
        assert any("correlation guard" in r for r in reasons)


@pytest.mark.skipif(not EURUSD_CACHE.exists(), reason="EURUSD cTrader cache missing")
class TestScannerTierInvariance:
    @staticmethod
    def _scan(filters):
        import pandas as pd

        from smc_detector import Candle, scan_setups

        df = pd.read_csv(EURUSD_CACHE).tail(4000)
        df["Datetime"] = pd.to_datetime(df["Datetime"], utc=False)
        candles = [
            Candle(
                time=row.Datetime.to_pydatetime().replace(tzinfo=None),
                open=row.Open,
                high=row.High,
                low=row.Low,
                close=row.Close,
                volume=getattr(row, "Volume", 0.0) or 0.0,
            )
            for row in df.itertuples()
        ]
        return scan_setups(
            symbol="EURUSD=X",
            days=60,
            interval="15m",
            session="both",
            min_rr=2.0,
            strategy_mode="legacy",
            scan_filters=filters,
            candles=candles,
            correlated=[None] * len(candles),
            quiet=True,
        )

    def test_a_tier_is_additive_and_aplus_takes_precedence(self):
        base_filters = {"chop_efficiency_min": 0.15, "chop_max_pivots": 9}
        baseline = self._scan(dict(base_filters))
        tiered = self._scan(
            {**base_filters, "a_tier": "mid", "a_mid_lo": 0.45, "a_mid_hi": 0.55}
        )

        assert all(s.tier == "A+" for s in baseline)
        ap = [s for s in tiered if s.tier == "A+"]
        a = [s for s in tiered if s.tier == "A"]

        key = lambda s: (s.time, s.direction, s.entry_price, s.stop_loss)  # noqa: E731
        assert {key(s) for s in ap} == {key(s) for s in baseline}, "A+ subset changed"
        assert all(not s.is_aplus for s in a), "A rows must not claim is_aplus"
        # No duplicate signals across tiers
        assert len({key(s) for s in tiered}) == len(tiered)
