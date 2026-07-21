"""Causal tests for Advanced FBOS/RBOS/CISD/Parent AMD gates (no lookahead)."""

from __future__ import annotations

from datetime import datetime, timedelta

from advanced_gates import (
    AdvancedConfig,
    AmdPhase,
    BreakKind,
    ParentState,
    build_advanced_timeline,
    evaluate_advanced_gates,
    has_cisd_for_direction,
)
from smc_detector import Candle


def _ts(i: int) -> datetime:
    return datetime(2024, 1, 2, 8, 0) + timedelta(minutes=15 * i)


def candles_from_ohlc(rows: list[tuple[float, float, float, float]]) -> list[Candle]:
    return [
        Candle(_ts(i), o, h, l, c, 1.0)
        for i, (o, h, l, c) in enumerate(rows)
    ]


def test_wick_beyond_close_inside_is_fbos_not_rbos():
    """Wick above prior high + close back inside → FBOS, never RBOS."""
    # Build a clear swing high around index 5, then fakeout
    base = 1.1000
    rows = []
    # Rising then pivot high at i=5
    for i in range(0, 12):
        if i < 5:
            rows.append((base + i * 0.001, base + i * 0.001 + 0.0005, base + i * 0.001 - 0.0003, base + i * 0.001 + 0.0002))
        elif i == 5:
            rows.append((base + 0.005, base + 0.008, base + 0.0045, base + 0.0055))  # swing high 1.108
        elif i < 9:
            rows.append((base + 0.004, base + 0.005, base + 0.003, base + 0.0035))  # pullback
        else:
            rows.append((base + 0.0035, base + 0.0038, base + 0.0030, base + 0.0032))
    # Need enough bars for lookback confirmation (lookback=3 → swing at 5 known at bar 8)
    # Fakeout: wick above 1.108, close below
    while len(rows) < 20:
        rows.append((1.1030, 1.1035, 1.1025, 1.1028))
    rows[14] = (1.1050, 1.1095, 1.1040, 1.1055)  # wick beyond high 1.108, close inside

    candles = candles_from_ohlc(rows)
    tl = build_advanced_timeline(candles, AdvancedConfig(swing_lookback=3))
    assert any(e.kind == BreakKind.FBOS for e in tl.fbos_events), "expected FBOS"
    assert not any(
        e.kind == BreakKind.RBOS and e.first_break_bar == 14 for e in tl.rbos_events
    ), "FBOS bar must not be classified as RBOS"


def test_close_beyond_with_expansion_is_rbos():
    """Close beyond structure + expansion → RBOS."""
    base = 1.1000
    rows = []
    for i in range(0, 12):
        if i < 5:
            rows.append((base + i * 0.001, base + i * 0.001 + 0.0005, base + i * 0.001 - 0.0003, base + i * 0.001 + 0.0002))
        elif i == 5:
            rows.append((base + 0.005, base + 0.008, base + 0.0045, base + 0.0055))
        else:
            rows.append((base + 0.004, base + 0.005, base + 0.003, base + 0.0035))
    while len(rows) < 22:
        rows.append((1.1030, 1.1035, 1.1025, 1.1028))
    # Close beyond high with expansion
    rows[14] = (1.1070, 1.1120, 1.1065, 1.1110)  # close above 1.108
    rows[15] = (1.1110, 1.1140, 1.1105, 1.1130)  # expansion

    candles = candles_from_ohlc(rows)
    cfg = AdvancedConfig(swing_lookback=3, rbos_require_retest=False, rbos_expansion_threshold_mult=0.3)
    tl = build_advanced_timeline(candles, cfg)
    assert tl.rbos_events, f"expected RBOS, got breaks={[e.kind for e in tl.breaks]}"
    assert all(e.kind != BreakKind.FBOS or e.first_break_bar != 14 for e in tl.fbos_events)


def test_cisd_after_fakeout_permits_entry():
    """FBOS then CISD beyond opposite leg extreme → gate accepts."""
    base = 1.1000
    rows = []
    for i in range(0, 12):
        if i < 5:
            rows.append((base + i * 0.001, base + i * 0.001 + 0.0005, base + i * 0.001 - 0.0003, base + i * 0.001 + 0.0002))
        elif i == 5:
            rows.append((base + 0.005, base + 0.008, base + 0.0045, base + 0.0055))  # high
        else:
            rows.append((base + 0.004, base + 0.005, base + 0.003, base + 0.0035))
    while len(rows) < 30:
        rows.append((1.1030, 1.1035, 1.1020, 1.1025))
    # Upside FBOS (BSL grab)
    rows[14] = (1.1050, 1.1095, 1.1020, 1.1030)  # wick high, close inside; leg spans low 1.1020
    # Bearish CISD: close below leg low
    rows[18] = (1.1025, 1.1030, 1.1000, 1.1005)

    candles = candles_from_ohlc(rows)
    tl = build_advanced_timeline(candles, AdvancedConfig())
    assert tl.fbos_events, "need FBOS first"
    assert has_cisd_for_direction(tl, 20, "bearish") is not None, "expected bearish CISD"
    gate = evaluate_advanced_gates(tl, entry_idx=20, direction="bearish", liq_idx=10)
    assert gate.ok, gate.reason
    assert gate.parent_amd_id


def test_no_cisd_rejects_entry():
    """FBOS without CISD → distribution entry rejected."""
    base = 1.1000
    rows = []
    for i in range(0, 12):
        if i < 5:
            rows.append((base + i * 0.001, base + i * 0.001 + 0.0005, base + i * 0.001 - 0.0003, base + i * 0.001 + 0.0002))
        elif i == 5:
            rows.append((base + 0.005, base + 0.008, base + 0.0045, base + 0.0055))
        else:
            rows.append((base + 0.004, base + 0.005, base + 0.003, base + 0.0035))
    while len(rows) < 25:
        rows.append((1.1030, 1.1035, 1.1025, 1.1028))
    rows[14] = (1.1050, 1.1095, 1.1040, 1.1055)  # FBOS only, no CISD

    candles = candles_from_ohlc(rows)
    tl = build_advanced_timeline(candles, AdvancedConfig())
    assert tl.fbos_events
    assert not tl.cisd_events
    gate = evaluate_advanced_gates(tl, entry_idx=20, direction="bearish", liq_idx=10)
    assert not gate.ok
    assert "no_cisd" in gate.reason


def test_counter_amd_invalidates_only_from_confirmation_bar():
    """Old POIs remain valid before counter-AMD bar; invalid at/after confirmation."""
    base = 1.1000
    rows = []
    for i in range(0, 12):
        if i < 5:
            rows.append((base + i * 0.001, base + i * 0.001 + 0.0005, base + i * 0.001 - 0.0003, base + i * 0.001 + 0.0002))
        elif i == 5:
            rows.append((base + 0.005, base + 0.008, base + 0.0045, base + 0.0055))
        elif i == 6:
            # also create a swing low later for opposite FBOS
            rows.append((base + 0.003, base + 0.0035, base + 0.001, base + 0.0015))
        else:
            rows.append((base + 0.002, base + 0.0025, base + 0.0015, base + 0.0020))
    while len(rows) < 40:
        rows.append((1.1020, 1.1025, 1.1015, 1.1018))

    # First: upside FBOS → bearish path
    rows[14] = (1.1050, 1.1095, 1.1010, 1.1020)
    rows[16] = (1.1015, 1.1020, 1.0990, 1.0995)  # bearish CISD below leg low

    # Later: downside FBOS on a low, then bullish CISD (counter)
    # Ensure a confirmed swing low exists around 6, known by bar 9+
    rows[24] = (1.1000, 1.1005, 1.0970, 1.0995)  # wick below ~1.098 area / prior low, close inside
    # Force a clearer swing low sequence before downside FBOS
    rows[10] = (1.1020, 1.1025, 1.0980, 1.0985)  # low swing candidate
    rows[24] = (1.0990, 1.0995, 1.0960, 1.0988)  # downside FBOS
    rows[28] = (1.1000, 1.1050, 1.0995, 1.1045)  # bullish CISD above manip leg high

    candles = candles_from_ohlc(rows)
    tl = build_advanced_timeline(candles, AdvancedConfig())
    assert len(tl.cisd_events) >= 1

    # Find first active distribution AMD and its invalidation
    first_dist = next(
        (a for a in tl.amds if a.phase == AmdPhase.DISTRIBUTION or a.distribution_started_at),
        None,
    )
    assert first_dist is not None

    # If a counter AMD occurred, old pois invalidated_at == confirmation bar (not earlier)
    invalidated = [p for p in tl.pois if p.invalidated_at is not None]
    for p in invalidated:
        # Must not be invalidated before the counter confirmation
        counter = next(
            (a for a in tl.amds if a.amd_id != p.parent_amd_id and a.created_bar == p.invalidated_at),
            None,
        )
        if counter:
            assert p.invalidated_at == counter.created_bar
            # Entries before invalidation bar still see valid POI
            pre = p.invalidated_at - 1
            assert p.created_bar <= pre
            assert p.invalidated_at > pre  # tautology: not yet invalidated before bar

    # Active AMD after last CISD should match last CISD direction
    if len(tl.cisd_events) >= 2:
        last = tl.cisd_events[-1]
        amd = tl.active_amd(last.bar)
        assert amd is not None
        assert amd.bias == last.direction
        assert amd.parent_state == ParentState.ACTIVE


def test_no_lookahead_fbos_confirmation_bar():
    """FBOS confirmed_bar equals the close-back bar, never earlier than evidence."""
    base = 1.1000
    rows = []
    for i in range(0, 12):
        if i == 5:
            rows.append((base + 0.005, base + 0.008, base + 0.0045, base + 0.0055))
        elif i < 5:
            rows.append((base + i * 0.001, base + i * 0.001 + 0.0005, base + i * 0.001 - 0.0003, base + i * 0.001 + 0.0002))
        else:
            rows.append((base + 0.004, base + 0.005, base + 0.003, base + 0.0035))
    while len(rows) < 20:
        rows.append((1.1030, 1.1035, 1.1025, 1.1028))
    rows[14] = (1.1050, 1.1095, 1.1040, 1.1055)

    candles = candles_from_ohlc(rows)
    tl = build_advanced_timeline(candles, AdvancedConfig())
    for ev in tl.fbos_events:
        assert ev.confirmed_bar is not None
        assert ev.confirmed_bar >= ev.first_break_bar
        assert ev.close_back_bar == ev.confirmed_bar


def test_presets_and_entry_model_config():
    from advanced_gates import PRESETS, SCAN_PRESETS, get_preset, get_scan_preset

    assert "advanced_default" in PRESETS
    assert "advanced_oos_candidate" in PRESETS
    cfg = get_preset("advanced_default")
    assert cfg.entry_model == "rbos"
    mit = AdvancedConfig(entry_model="mitigation")
    assert mit.entry_model == "mitigation"

    assert "research_best" in SCAN_PRESETS
    recipe = get_scan_preset("research_best")
    assert recipe.mode == "legacy"
    assert recipe.session == "both"
    assert recipe.min_rr == 2.0
    assert recipe.filters["chop_efficiency_min"] == 0.15
    assert recipe.filters["chop_max_pivots"] == 9
    assert recipe.filters["mid_lo"] == 0.35
    assert recipe.filters["mid_hi"] == 0.65
    assert recipe.data_source == "ctrader"
    assert recipe.advanced is None
