import unittest

from executor_core import AccountSnapshot, TradeIntent, evaluate_trade


class ExecutorSafetyTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = AccountSnapshot(
            host="demo",
            ctid_account_id=47_820_966,
            trader_login=10_123_191,
            balance=10_927.60,
            open_positions=0,
            daily_realized_pnl=0.0,
        )
        self.intent = TradeIntent(
            setup_id="setup-001",
            symbol="EURUSD",
            side="long",
            entry=1.1000,
            stop_loss=1.0990,
            take_profit=1.1020,
        )

    def test_approves_valid_dry_run_and_sizes_down_to_step(self):
        decision = evaluate_trade(self.intent, self.snapshot)

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reasons, ())
        self.assertEqual(decision.risk_amount, 27.32)
        self.assertEqual(decision.risk_reward, 2.0)
        self.assertEqual(decision.stop_distance_pips, 10.0)
        self.assertEqual(decision.lots, 0.27)
        self.assertEqual(decision.units, 27_000)

    def test_blocks_any_live_host(self):
        snapshot = AccountSnapshot(**{**self.snapshot.__dict__, "host": "live"})
        self.assertIn("live hosts are blocked", evaluate_trade(self.intent, snapshot).reasons)

    def test_blocks_wrong_account_and_login(self):
        snapshot = AccountSnapshot(
            **{
                **self.snapshot.__dict__,
                "ctid_account_id": 1,
                "trader_login": 2,
            }
        )
        reasons = evaluate_trade(self.intent, snapshot).reasons
        self.assertIn("cTrader account ID is not allowlisted", reasons)
        self.assertIn("trader login is not Fusion demo 10123191", reasons)

    def test_blocks_non_allowlisted_and_non_aplus(self):
        intent = TradeIntent(**{**self.intent.__dict__, "symbol": "GBPUSD", "is_aplus": False})
        reasons = evaluate_trade(intent, self.snapshot).reasons
        self.assertTrue(any("not allowlisted" in r for r in reasons))
        self.assertIn("only A+ setups are allowed", reasons)

    def test_allows_xauusd_with_matching_instrument(self):
        from executor_core import default_instrument
        intent = TradeIntent(
            setup_id="gold-001",
            symbol="XAUUSD",
            side="long",
            entry=3300.0,
            stop_loss=3295.0,
            take_profit=3310.0,
        )
        decision = evaluate_trade(
            intent, self.snapshot, instrument=default_instrument("XAUUSD")
        )
        self.assertTrue(decision.allowed, decision.reasons)
        self.assertEqual(decision.normalized_symbol, "XAUUSD")

    def test_per_symbol_position_limit_allows_other_symbol(self):
        # EUR position open should not block XAU if open_positions_for_symbol=0
        snapshot = AccountSnapshot(
            **{**self.snapshot.__dict__, "open_positions": 1, "open_positions_for_symbol": 0}
        )
        from executor_core import default_instrument
        intent = TradeIntent(
            setup_id="gold-002",
            symbol="XAUUSD",
            side="long",
            entry=3300.0,
            stop_loss=3295.0,
            take_profit=3310.0,
        )
        decision = evaluate_trade(
            intent, snapshot, instrument=default_instrument("XAUUSD")
        )
        self.assertTrue(decision.allowed, decision.reasons)

    def test_blocks_risk_above_quarter_percent(self):
        intent = TradeIntent(**{**self.intent.__dict__, "risk_percent": 0.26})
        self.assertIn(
            "risk_percent exceeds 0.25% cap",
            evaluate_trade(intent, self.snapshot).reasons,
        )

    def test_blocks_invalid_protection_and_rr(self):
        intent = TradeIntent(
            **{
                **self.intent.__dict__,
                "stop_loss": 1.1010,
                "take_profit": 1.1005,
            }
        )
        reasons = evaluate_trade(intent, self.snapshot).reasons
        self.assertIn("long requires stop_loss < entry < take_profit", reasons)
        self.assertIn("risk/reward is below 2.0", reasons)

    def test_blocks_when_a_position_is_already_open(self):
        snapshot = AccountSnapshot(
            **{**self.snapshot.__dict__, "open_positions": 1, "open_positions_for_symbol": 1}
        )
        self.assertIn(
            "maximum open-position limit reached for symbol",
            evaluate_trade(self.intent, snapshot).reasons,
        )

    def test_blocks_when_pending_orders_exist(self):
        snapshot = AccountSnapshot(**{**self.snapshot.__dict__, "pending_orders": 1})
        self.assertIn(
            "pending orders must be flat before a new setup",
            evaluate_trade(self.intent, snapshot).reasons,
        )

    def test_blocks_live_account_flag_and_non_fusion_broker(self):
        snapshot = AccountSnapshot(
            **{
                **self.snapshot.__dict__,
                "is_live": True,
                "broker_name": "FxPro",
            }
        )
        reasons = evaluate_trade(self.intent, snapshot).reasons
        self.assertIn("live accounts are blocked", reasons)
        self.assertIn("broker is not Fusion Markets", reasons)

    def test_blocks_duplicate_setup(self):
        self.assertIn(
            "duplicate setup_id",
            evaluate_trade(self.intent, self.snapshot, {"setup-001"}).reasons,
        )

    def test_blocks_at_daily_loss_limit(self):
        snapshot = AccountSnapshot(
            **{
                **self.snapshot.__dict__,
                "balance": 9_900.0,
                "daily_realized_pnl": -100.0,
            }
        )
        self.assertIn(
            "daily loss limit reached",
            evaluate_trade(self.intent, snapshot).reasons,
        )

    def test_blocks_when_new_risk_would_cross_daily_limit(self):
        snapshot = AccountSnapshot(
            **{
                **self.snapshot.__dict__,
                "balance": 9_915.0,
                "daily_realized_pnl": -85.0,
            }
        )
        self.assertIn(
            "proposed risk would exceed the daily loss limit",
            evaluate_trade(self.intent, snapshot).reasons,
        )

    def test_blocks_incomplete_daily_history(self):
        snapshot = AccountSnapshot(
            **{**self.snapshot.__dict__, "deal_history_complete": False}
        )
        self.assertIn(
            "daily deal history is incomplete",
            evaluate_trade(self.intent, snapshot).reasons,
        )

    def test_blocks_volume_below_broker_minimum(self):
        intent = TradeIntent(
            **{
                **self.intent.__dict__,
                "entry": 1.2,
                "stop_loss": 1.1,
                "take_profit": 1.4,
            }
        )
        self.assertIn(
            "calculated volume is below the broker minimum",
            evaluate_trade(intent, self.snapshot).reasons,
        )



    def test_allows_gbpusd_min_volume(self):
        from executor_core import default_instrument

        intent = TradeIntent(
            **{**self.intent.__dict__, "symbol": "GBPUSD", "setup_id": "gbp-001"}
        )
        decision = evaluate_trade(
            intent, self.snapshot, instrument=default_instrument("GBPUSD")
        )
        self.assertTrue(decision.allowed, decision.reasons)
        self.assertEqual(decision.normalized_symbol, "GBPUSD")
        self.assertEqual(decision.broker_volume_cents, 100_000)

    def test_usdx_pip_scaling_and_min_volume(self):
        from executor_core import default_instrument

        inst = default_instrument("USDX")
        self.assertEqual(inst.pip_size, 0.01)
        self.assertEqual(inst.pip_position, 2)
        self.assertEqual(inst.min_volume_cents, 100)
        intent = TradeIntent(
            setup_id="usdx-001",
            symbol="USDX",
            side="short",
            entry=98.500,
            stop_loss=98.650,
            take_profit=98.200,
        )
        decision = evaluate_trade(intent, self.snapshot, instrument=inst)
        self.assertTrue(decision.allowed, decision.reasons)
        self.assertEqual(decision.normalized_symbol, "USDX")
        self.assertEqual(decision.stop_distance_pips, 15.0)
        # USDX deliberately trades broker minimum volume only.
        self.assertEqual(decision.broker_volume_cents, 100)

    def test_usdjpy_pip_scaling(self):
        from executor_core import default_instrument

        inst = default_instrument("USDJPY")
        self.assertEqual(inst.pip_size, 0.01)
        self.assertEqual(inst.pip_position, 2)
        intent = TradeIntent(
            setup_id="jpy-001",
            symbol="USDJPY",
            side="long",
            entry=150.000,
            stop_loss=149.850,
            take_profit=150.300,
        )
        decision = evaluate_trade(intent, self.snapshot, instrument=inst)
        self.assertTrue(decision.allowed, decision.reasons)
        self.assertEqual(decision.stop_distance_pips, 15.0)


if __name__ == "__main__":
    unittest.main()
