"""
Causal Advanced gates: FBOS/RBOS + CISD + Parent AMD lifecycle.

Defaults are documented choices from SALIM_V3_RULES / KAANQ_MASTERY — not unique.
All transitions use completed bars only; no retroactive invalidation.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Optional

STRATEGY_VERSION = "1.2.0"
STRATEGY_MODE_LEGACY = "legacy"
STRATEGY_MODE_ADVANCED = "advanced"


class BreakKind(str, Enum):
    PENDING = "pending"
    FBOS = "fbos"
    RBOS_CANDIDATE = "rbos_candidate"
    RBOS = "rbos"
    FAILED = "failed"


class AmdPhase(str, Enum):
    ACCUMULATION = "accumulation"
    MANIPULATION = "manipulation"
    DISTRIBUTION = "distribution"


class ParentState(str, Enum):
    ACTIVE = "active"
    INVALIDATED = "invalidated"
    PROSPECTIVE = "prospective"


@dataclass
class AdvancedConfig:
    """Configurable defaults for Advanced mode (documented, not unique)."""

    fbos_close_back_max_bars: int = 3
    rbos_close_buffer: float = 0.0  # absolute price; 0 = any close beyond structure
    rbos_require_retest: bool = False
    rbos_retest_tolerance: float = 0.0002  # ~2 pips EURUSD
    rbos_expansion_threshold_mult: float = 0.5  # fraction of break excursion / leg range
    swing_lookback: int = 3
    require_cisd: bool = True
    require_parent_amd: bool = True
    # Entry model (SALIM_V3_RULES):
    #   rbos — require RBOS/CISD confirmation after liq (default Advanced)
    #   mitigation — allow entry when manipulation ended (CISD/AMD) without RBOS
    entry_model: str = "rbos"


# Default Frequency-track scan knobs (production Legacy / Advanced shared).
DEFAULT_SCAN_FILTERS: dict = {
    "chop_efficiency_min": 0.22,
    "chop_max_pivots": 7,
    "mid_lo": 0.35,
    "mid_hi": 0.65,
    "asia_aggressive": False,
}


@dataclass
class ScanRecipe:
    """Full scan preset: mode + session + RR + Frequency filters (+ optional Advanced gates)."""

    name: str
    mode: str = STRATEGY_MODE_LEGACY  # legacy | advanced
    session: str = "both"
    min_rr: float = 2.0
    filters: dict = field(default_factory=lambda: dict(DEFAULT_SCAN_FILTERS))
    advanced: Optional[AdvancedConfig] = None
    data_source: str = "ctrader"  # ctrader | yahoo
    interval: str = "15m"
    label: str = ""


# Named Advanced gate presets — opt-in only; defaults above stay unchanged unless selected.
PRESETS: dict[str, AdvancedConfig] = {
    "advanced_default": AdvancedConfig(),
    # Research-named slots filled after OOS study (see scripts/parameter_study.py).
    # Placeholder identical to default until a robust Advanced OOS winner is confirmed.
    "advanced_oos_candidate": AdvancedConfig(),
}


# Full scan recipes (mode + chop/mid knobs). research_best = OOS overall winner.
SCAN_PRESETS: dict[str, ScanRecipe] = {
    # OOS best: legacy_chop_loose on cTrader EURUSD M15 (n=39, E[R]=1.621, PF=3.53).
    # Do NOT wire this to Advanced gates_off — that was weaker (E[R]=0.39).
    "research_best": ScanRecipe(
        name="legacy_chop_loose",
        mode=STRATEGY_MODE_LEGACY,
        session="both",
        min_rr=2.0,
        filters={
            **DEFAULT_SCAN_FILTERS,
            "chop_efficiency_min": 0.15,
            "chop_max_pivots": 9,
        },
        advanced=None,
        data_source="ctrader",
        interval="15m",
        label="Legacy chop_loose — OOS best (cTrader M15)",
    ),
    # A+ (unchanged research_best) + A tier: ONE relaxation — premium/discount
    # zone widened 0.35/0.65 -> 0.45/0.55 for A-labelled entries only.
    # Train-selected from {mid 40/60, mid 42/58, mid 45/55, idm_flex}; OOS
    # (9 symbols, 3-fold WF): A-only n=110, E[R]=1.354, PF=3.26; combined
    # n 387->497 (x1.28). See reports/tier_study.md. A+ subset bit-identical.
    "research_best_a": ScanRecipe(
        name="legacy_chop_loose_a_mid_45_55",
        mode=STRATEGY_MODE_LEGACY,
        session="both",
        min_rr=2.0,
        filters={
            **DEFAULT_SCAN_FILTERS,
            "chop_efficiency_min": 0.15,
            "chop_max_pivots": 9,
            "a_tier": "mid",
            "a_mid_lo": 0.45,
            "a_mid_hi": 0.55,
        },
        advanced=None,
        data_source="ctrader",
        interval="15m",
        label="research_best + A tier (mid 0.45/0.55) — OOS PASS Jul 2026",
    ),
}


def get_preset(name: str) -> AdvancedConfig:
    key = (name or "").strip().lower()
    if key not in PRESETS:
        raise KeyError(f"Unknown preset '{name}'. Known: {sorted(PRESETS)}")
    return replace(PRESETS[key])


def get_scan_preset(name: str) -> ScanRecipe:
    key = (name or "").strip().lower()
    if key not in SCAN_PRESETS:
        raise KeyError(f"Unknown scan preset '{name}'. Known: {sorted(SCAN_PRESETS)}")
    recipe = SCAN_PRESETS[key]
    return ScanRecipe(
        name=recipe.name,
        mode=recipe.mode,
        session=recipe.session,
        min_rr=recipe.min_rr,
        filters=dict(recipe.filters),
        advanced=replace(recipe.advanced) if recipe.advanced is not None else None,
        data_source=recipe.data_source,
        interval=recipe.interval,
        label=recipe.label,
    )


@dataclass
class StructureBreakEvent:
    level: float
    swing_index: int
    break_direction: str  # 'bullish' = broke above high; 'bearish' = broke below low
    first_break_bar: int
    kind: BreakKind
    confirmed_bar: Optional[int] = None
    max_excursion: float = 0.0
    close_back_bar: Optional[int] = None
    expansion_bar: Optional[int] = None


@dataclass
class ManipulationLeg:
    sweep_bar: int
    sweep_direction: str  # direction of liquidity grab: 'up' (BSL) or 'down' (SSL)
    leg_high: float
    leg_low: float
    start_bar: int
    end_bar: int
    source: str = "IDM/FO"  # FO / IDM / EQ


@dataclass
class CisdEvent:
    bar: int
    direction: str  # trade direction enabled: bullish/bearish
    leg: ManipulationLeg
    level_broken: float


@dataclass
class OwnedPoi:
    poi_id: str
    parent_amd_id: str
    direction: str
    top: float
    bottom: float
    created_bar: int
    is_crown: bool = False
    invalidated_at: Optional[int] = None

    @property
    def valid(self) -> bool:
        return self.invalidated_at is None


@dataclass
class ParentAmd:
    amd_id: str
    bias: str  # bullish / bearish
    phase: AmdPhase
    parent_state: ParentState
    protected_high: float
    protected_low: float
    created_bar: int
    manipulation_ended_at: Optional[int] = None
    distribution_started_at: Optional[int] = None
    invalidated_at: Optional[int] = None
    crown_poi_id: Optional[str] = None


@dataclass
class AdvancedTimeline:
    """Forward-only event ledger built causally through the series."""

    config: AdvancedConfig
    breaks: list[StructureBreakEvent] = field(default_factory=list)
    fbos_events: list[StructureBreakEvent] = field(default_factory=list)
    rbos_events: list[StructureBreakEvent] = field(default_factory=list)
    cisd_events: list[CisdEvent] = field(default_factory=list)
    amds: list[ParentAmd] = field(default_factory=list)
    pois: list[OwnedPoi] = field(default_factory=list)
    _next_amd: int = 1
    _next_poi: int = 1
    _pending: list[StructureBreakEvent] = field(default_factory=list)
    _open_leg: Optional[ManipulationLeg] = None
    _active_amd_id: Optional[str] = None

    def active_amd(self, as_of_bar: int) -> Optional[ParentAmd]:
        for a in reversed(self.amds):
            if a.created_bar > as_of_bar:
                continue
            if a.parent_state == ParentState.INVALIDATED:
                if a.invalidated_at is not None and a.invalidated_at <= as_of_bar:
                    continue
            if a.parent_state == ParentState.ACTIVE and (
                a.invalidated_at is None or a.invalidated_at > as_of_bar
            ):
                return a
            if a.parent_state == ParentState.PROSPECTIVE:
                continue
        return None


def confirmed_swings(
    candles,
    lookback: int = 3,
    as_of: Optional[int] = None,
) -> list:
    """
    Causal swing points: a pivot at i is only known once bar i+lookback has closed.
    Returns SwingPoint-like objects via duck typing (index, price, kind).
    """
    from smc_detector import SwingPoint

    end = len(candles) if as_of is None else as_of + 1
    swings = []
    # Last confirmable pivot index is end - 1 - lookback
    last_i = end - 1 - lookback
    for i in range(lookback, max(lookback, last_i + 1)):
        if i + lookback >= end:
            break
        wh = [c.high for c in candles[i - lookback : i + lookback + 1]]
        wl = [c.low for c in candles[i - lookback : i + lookback + 1]]
        if candles[i].high == max(wh):
            swings.append(SwingPoint(i, candles[i].time, candles[i].high, "high"))
        if candles[i].low == min(wl):
            swings.append(SwingPoint(i, candles[i].time, candles[i].low, "low"))
    return swings


def _excursion(c, level: float, break_direction: str) -> float:
    if break_direction == "bullish":
        return max(0.0, c.high - level)
    return max(0.0, level - c.low)


def _close_beyond(c, level: float, break_direction: str, buf: float) -> bool:
    if break_direction == "bullish":
        return c.close > level + buf
    return c.close < level - buf


def _close_inside(c, level: float, break_direction: str) -> bool:
    if break_direction == "bullish":
        return c.close <= level
    return c.close >= level


def _wick_through(c, level: float, break_direction: str) -> bool:
    if break_direction == "bullish":
        return c.high > level
    return c.low < level


def _is_confirmed_swing_at(candles, pivot: int, lookback: int, kind: str) -> bool:
    """True if pivot is a confirmed swing high/low given lookback confirmation bars."""
    if pivot < lookback or pivot + lookback >= len(candles):
        return False
    window = candles[pivot - lookback : pivot + lookback + 1]
    if kind == "high":
        px = candles[pivot].high
        return px == max(c.high for c in window)
    px = candles[pivot].low
    return px == min(c.low for c in window)


def build_advanced_timeline(candles, cfg: Optional[AdvancedConfig] = None) -> AdvancedTimeline:
    """
    Walk bars forward and classify FBOS/RBOS, CISD, and Parent AMD transitions.
    """
    cfg = cfg or AdvancedConfig()
    tl = AdvancedTimeline(config=cfg)
    n = len(candles)
    if n < cfg.swing_lookback * 2 + 2:
        return tl

    from smc_detector import SwingPoint

    # Track which swing levels are still live for break detection.
    # Swings are maintained incrementally (O(1) new confirmations per bar) —
    # equivalent to confirmed_swings(..., as_of=bar) but O(n) overall, not O(n²).
    consumed_highs: set[int] = set()
    consumed_lows: set[int] = set()
    swings: list = []
    lb = cfg.swing_lookback

    for bar in range(cfg.swing_lookback * 2, n):
        # Newly confirmable pivot once bar == pivot + lookback
        pivot = bar - lb
        if pivot >= lb:
            if _is_confirmed_swing_at(candles, pivot, lb, "high"):
                swings.append(SwingPoint(pivot, candles[pivot].time, candles[pivot].high, "high"))
            if _is_confirmed_swing_at(candles, pivot, lb, "low"):
                swings.append(SwingPoint(pivot, candles[pivot].time, candles[pivot].low, "low"))
        c = candles[bar]

        # --- Update pending breaks / classify FBOS or RBOS ---
        still_pending: list[StructureBreakEvent] = []
        for ev in tl._pending:
            bars_since = bar - ev.first_break_bar
            ev.max_excursion = max(ev.max_excursion, _excursion(c, ev.level, ev.break_direction))

            if ev.kind == BreakKind.PENDING:
                # Same-bar or soon close back inside → FBOS
                if _close_inside(c, ev.level, ev.break_direction) and bars_since <= cfg.fbos_close_back_max_bars:
                    ev.kind = BreakKind.FBOS
                    ev.confirmed_bar = bar
                    ev.close_back_bar = bar
                    tl.breaks.append(ev)
                    tl.fbos_events.append(ev)
                    _on_fbos(tl, candles, ev, bar)
                    continue
                # Close beyond → RBOS candidate
                if _close_beyond(c, ev.level, ev.break_direction, cfg.rbos_close_buffer):
                    ev.kind = BreakKind.RBOS_CANDIDATE
                    still_pending.append(ev)
                    continue
                if bars_since > cfg.fbos_close_back_max_bars:
                    # Never closed back inside and never closed beyond cleanly — drop
                    ev.kind = BreakKind.FAILED
                    ev.confirmed_bar = bar
                    tl.breaks.append(ev)
                    continue
                still_pending.append(ev)
                continue

            if ev.kind == BreakKind.RBOS_CANDIDATE:
                # Failure: close back through before confirmation → may become FBOS
                if _close_inside(c, ev.level, ev.break_direction):
                    ev.kind = BreakKind.FBOS
                    ev.confirmed_bar = bar
                    ev.close_back_bar = bar
                    tl.breaks.append(ev)
                    tl.fbos_events.append(ev)
                    _on_fbos(tl, candles, ev, bar)
                    continue

                # Expansion confirmation
                threshold = max(cfg.rbos_close_buffer, ev.max_excursion * cfg.rbos_expansion_threshold_mult)
                expanded = ev.max_excursion >= threshold and threshold > 0
                if threshold == 0:
                    expanded = True
                # Also accept expansion measured from close beyond on later bars
                if ev.break_direction == "bullish":
                    expanded = expanded or (c.high - ev.level) >= max(
                        threshold, abs(candles[ev.first_break_bar].high - ev.level) * cfg.rbos_expansion_threshold_mult
                    )
                else:
                    expanded = expanded or (ev.level - c.low) >= max(
                        threshold, abs(ev.level - candles[ev.first_break_bar].low) * cfg.rbos_expansion_threshold_mult
                    )

                retest_ok = True
                if cfg.rbos_require_retest:
                    retest_ok = False
                    if ev.break_direction == "bullish":
                        if abs(c.low - ev.level) <= cfg.rbos_retest_tolerance and c.close >= ev.level - cfg.rbos_retest_tolerance:
                            retest_ok = True
                    else:
                        if abs(c.high - ev.level) <= cfg.rbos_retest_tolerance and c.close <= ev.level + cfg.rbos_retest_tolerance:
                            retest_ok = True

                # Default: close-beyond bar itself can confirm if expansion threshold met
                # (rbos_require_retest=False). Use max_excursion from the candidate bar onward.
                if expanded and retest_ok:
                    ev.kind = BreakKind.RBOS
                    ev.confirmed_bar = bar
                    ev.expansion_bar = bar
                    tl.breaks.append(ev)
                    tl.rbos_events.append(ev)
                    _on_rbos(tl, candles, ev, bar)
                    continue
                still_pending.append(ev)
                continue

            still_pending.append(ev)

        tl._pending = still_pending

        # --- Detect new penetrations of confirmed swings ---
        for sh in swings:
            if sh.kind != "high" or sh.index in consumed_highs or sh.index >= bar:
                continue
            if _wick_through(c, sh.price, "bullish"):
                consumed_highs.add(sh.index)
                ev = StructureBreakEvent(
                    level=sh.price,
                    swing_index=sh.index,
                    break_direction="bullish",
                    first_break_bar=bar,
                    kind=BreakKind.PENDING,
                    max_excursion=_excursion(c, sh.price, "bullish"),
                )
                # Immediate same-bar FBOS: wick above, close back inside
                if _close_inside(c, sh.price, "bullish"):
                    ev.kind = BreakKind.FBOS
                    ev.confirmed_bar = bar
                    ev.close_back_bar = bar
                    tl.breaks.append(ev)
                    tl.fbos_events.append(ev)
                    _on_fbos(tl, candles, ev, bar)
                elif _close_beyond(c, sh.price, "bullish", cfg.rbos_close_buffer):
                    ev.kind = BreakKind.RBOS_CANDIDATE
                    tl._pending.append(ev)
                else:
                    tl._pending.append(ev)

        for sl in swings:
            if sl.kind != "low" or sl.index in consumed_lows or sl.index >= bar:
                continue
            if _wick_through(c, sl.price, "bearish"):
                consumed_lows.add(sl.index)
                ev = StructureBreakEvent(
                    level=sl.price,
                    swing_index=sl.index,
                    break_direction="bearish",
                    first_break_bar=bar,
                    kind=BreakKind.PENDING,
                    max_excursion=_excursion(c, sl.price, "bearish"),
                )
                if _close_inside(c, sl.price, "bearish"):
                    ev.kind = BreakKind.FBOS
                    ev.confirmed_bar = bar
                    ev.close_back_bar = bar
                    tl.breaks.append(ev)
                    tl.fbos_events.append(ev)
                    _on_fbos(tl, candles, ev, bar)
                elif _close_beyond(c, sl.price, "bearish", cfg.rbos_close_buffer):
                    ev.kind = BreakKind.RBOS_CANDIDATE
                    tl._pending.append(ev)
                else:
                    tl._pending.append(ev)

        # --- CISD: break opposite extreme of open manipulation leg ---
        _try_cisd(tl, candles, bar)

    return tl


def _on_fbos(tl: AdvancedTimeline, candles, ev: StructureBreakEvent, bar: int) -> None:
    """FBOS starts / extends manipulation; not a valid continuation BOS."""
    # Upside FBOS (broke high, closed back) → BSL grab → expect bearish CISD later
    # Downside FBOS → SSL grab → expect bullish CISD later
    if ev.break_direction == "bullish":
        sweep_dir = "up"
        start = ev.swing_index
        leg_high = max(candles[k].high for k in range(start, bar + 1))
        leg_low = min(candles[k].low for k in range(start, bar + 1))
    else:
        sweep_dir = "down"
        start = ev.swing_index
        leg_high = max(candles[k].high for k in range(start, bar + 1))
        leg_low = min(candles[k].low for k in range(start, bar + 1))

    tl._open_leg = ManipulationLeg(
        sweep_bar=bar,
        sweep_direction=sweep_dir,
        leg_high=leg_high,
        leg_low=leg_low,
        start_bar=start,
        end_bar=bar,
        source="FO",
    )
    # Enter / stay in manipulation on a prospective or active AMD
    amd = tl.active_amd(bar)
    if amd is None or amd.phase == AmdPhase.DISTRIBUTION:
        # Start new prospective cycle
        amd_id = f"AMD-{tl._next_amd}"
        tl._next_amd += 1
        amd = ParentAmd(
            amd_id=amd_id,
            bias="bearish" if sweep_dir == "up" else "bullish",
            phase=AmdPhase.MANIPULATION,
            parent_state=ParentState.PROSPECTIVE,
            protected_high=leg_high,
            protected_low=leg_low,
            created_bar=bar,
        )
        tl.amds.append(amd)
        tl._active_amd_id = amd_id
    else:
        amd.phase = AmdPhase.MANIPULATION
        amd.protected_high = max(amd.protected_high, leg_high)
        amd.protected_low = min(amd.protected_low, leg_low)


def _on_rbos(tl: AdvancedTimeline, candles, ev: StructureBreakEvent, bar: int) -> None:
    """Confirmed RBOS may itself act as CISD if it breaks the open leg opposite extreme."""
    # Handled primarily in _try_cisd; mark nothing extra here.
    _ = (tl, candles, ev, bar)


def _try_cisd(tl: AdvancedTimeline, candles, bar: int) -> None:
    leg = tl._open_leg
    if leg is None:
        return
    cfg = tl.config
    c = candles[bar]
    # After upside FO (BSL): bearish CISD = close below leg_low
    # After downside FO (SSL): bullish CISD = close above leg_high
    if leg.sweep_direction == "up":
        level = leg.leg_low
        if not _close_beyond(c, level, "bearish", cfg.rbos_close_buffer):
            return
        direction = "bearish"
        level_broken = level
    else:
        level = leg.leg_high
        if not _close_beyond(c, level, "bullish", cfg.rbos_close_buffer):
            return
        direction = "bullish"
        level_broken = level

    # Expansion hold: require some follow-through same bar or treat close-beyond as enough
    # when rbos_require_retest is False (default).
    cisd = CisdEvent(bar=bar, direction=direction, leg=leg, level_broken=level_broken)
    tl.cisd_events.append(cisd)
    tl._open_leg = None

    # Activate / replace Parent AMD
    old = tl.active_amd(bar)
    if old is not None and old.bias != direction and old.parent_state == ParentState.ACTIVE:
        _counter_amd_replace(tl, old, direction, bar, leg)
    else:
        _begin_distribution(tl, direction, bar, leg)


def _begin_distribution(
    tl: AdvancedTimeline,
    direction: str,
    bar: int,
    leg: ManipulationLeg,
) -> None:
    amd = None
    if tl._active_amd_id:
        for a in tl.amds:
            if a.amd_id == tl._active_amd_id and a.invalidated_at is None:
                amd = a
                break
    if amd is None:
        amd_id = f"AMD-{tl._next_amd}"
        tl._next_amd += 1
        amd = ParentAmd(
            amd_id=amd_id,
            bias=direction,
            phase=AmdPhase.DISTRIBUTION,
            parent_state=ParentState.ACTIVE,
            protected_high=leg.leg_high,
            protected_low=leg.leg_low,
            created_bar=bar,
            manipulation_ended_at=bar,
            distribution_started_at=bar,
        )
        tl.amds.append(amd)
        tl._active_amd_id = amd_id
    else:
        amd.bias = direction
        amd.phase = AmdPhase.DISTRIBUTION
        amd.parent_state = ParentState.ACTIVE
        amd.manipulation_ended_at = bar
        amd.distribution_started_at = bar
        amd.protected_high = leg.leg_high
        amd.protected_low = leg.leg_low

    poi_id = f"POI-{tl._next_poi}"
    tl._next_poi += 1
    crown = OwnedPoi(
        poi_id=poi_id,
        parent_amd_id=amd.amd_id,
        direction=direction,
        top=leg.leg_high,
        bottom=leg.leg_low,
        created_bar=bar,
        is_crown=True,
    )
    tl.pois.append(crown)
    amd.crown_poi_id = poi_id


def _counter_amd_replace(
    tl: AdvancedTimeline,
    old: ParentAmd,
    direction: str,
    bar: int,
    leg: ManipulationLeg,
) -> None:
    """Atomic forward-only invalidation then new crown (no retroactive rewrite)."""
    old.parent_state = ParentState.INVALIDATED
    old.invalidated_at = bar
    for p in tl.pois:
        if p.parent_amd_id == old.amd_id and p.invalidated_at is None:
            p.invalidated_at = bar

    amd_id = f"AMD-{tl._next_amd}"
    tl._next_amd += 1
    amd = ParentAmd(
        amd_id=amd_id,
        bias=direction,
        phase=AmdPhase.DISTRIBUTION,
        parent_state=ParentState.ACTIVE,
        protected_high=leg.leg_high,
        protected_low=leg.leg_low,
        created_bar=bar,
        manipulation_ended_at=bar,
        distribution_started_at=bar,
    )
    tl.amds.append(amd)
    tl._active_amd_id = amd_id

    poi_id = f"POI-{tl._next_poi}"
    tl._next_poi += 1
    crown = OwnedPoi(
        poi_id=poi_id,
        parent_amd_id=amd_id,
        direction=direction,
        top=leg.leg_high,
        bottom=leg.leg_low,
        created_bar=bar,
        is_crown=True,
    )
    tl.pois.append(crown)
    amd.crown_poi_id = poi_id


def is_fbos_break(tl: AdvancedTimeline, bos_index: int, direction: str) -> bool:
    """True if a mechanical BOS bar was classified as FBOS (not valid continuation)."""
    for ev in tl.fbos_events:
        if ev.confirmed_bar == bos_index and (
            (direction == "bullish" and ev.break_direction == "bullish")
            or (direction == "bearish" and ev.break_direction == "bearish")
        ):
            return True
    return False


def has_rbos_after_liq(
    tl: AdvancedTimeline,
    liq_idx: int,
    entry_idx: int,
    direction: str,
) -> bool:
    for ev in tl.rbos_events:
        if ev.confirmed_bar is None:
            continue
        if ev.confirmed_bar <= liq_idx or ev.confirmed_bar > entry_idx:
            continue
        if direction == "bullish" and ev.break_direction == "bullish":
            return True
        if direction == "bearish" and ev.break_direction == "bearish":
            return True
    # CISD also counts as RBOS confirmation of opposite leg
    for cisd in tl.cisd_events:
        if liq_idx < cisd.bar <= entry_idx and cisd.direction == direction:
            return True
    return False


def has_cisd_for_direction(
    tl: AdvancedTimeline,
    entry_idx: int,
    direction: str,
    lookback: int = 48,
) -> Optional[CisdEvent]:
    for cisd in reversed(tl.cisd_events):
        if cisd.bar > entry_idx:
            continue
        if entry_idx - cisd.bar > lookback:
            break
        if cisd.direction == direction:
            return cisd
    return None


@dataclass
class AdvancedGateResult:
    ok: bool
    reason: str
    parent_amd_id: Optional[str] = None
    cisd_bar: Optional[int] = None
    fbos_seen: bool = False


def evaluate_advanced_gates(
    tl: AdvancedTimeline,
    entry_idx: int,
    direction: str,
    liq_idx: int,
    cfg: Optional[AdvancedConfig] = None,
) -> AdvancedGateResult:
    """
    Gate a candidate entry that already passed Legacy A+ mechanical filters.
    Returns accept/reject with reason (causal as-of entry_idx).
    """
    cfg = cfg or tl.config
    trade_dir = "bullish" if direction in ("bullish", "long") else "bearish"
    entry_model = (cfg.entry_model or "rbos").strip().lower()

    # 1) Valid break after liq must be RBOS/CISD, not FBOS-only mechanical BOS
    #    Mitigation mode: skip hard RBOS requirement; still need CISD/AMD below.
    if entry_model != "mitigation":
        if not has_rbos_after_liq(tl, liq_idx, entry_idx, trade_dir):
            return AdvancedGateResult(False, "reject:no_rbos_cisd_after_liq")

    # 2) CISD confirmation required
    cisd = has_cisd_for_direction(tl, entry_idx, trade_dir)
    fbos_seen = any(
        ev.confirmed_bar is not None
        and liq_idx - 8 <= ev.confirmed_bar <= entry_idx
        for ev in tl.fbos_events
    )
    if cfg.require_cisd and cisd is None:
        return AdvancedGateResult(
            False, "reject:no_cisd", fbos_seen=fbos_seen
        )
    # Mitigation without CISD and without RBOS is too loose — require at least one
    if entry_model == "mitigation" and not cfg.require_cisd and cisd is None:
        if not has_rbos_after_liq(tl, liq_idx, entry_idx, trade_dir):
            return AdvancedGateResult(False, "reject:mitigation_no_end_signal", fbos_seen=fbos_seen)

    # 3) Parent AMD must be Active Distribution matching bias
    if cfg.require_parent_amd:
        amd = tl.active_amd(entry_idx)
        if amd is None:
            return AdvancedGateResult(False, "reject:no_active_parent_amd", fbos_seen=fbos_seen)
        if amd.phase != AmdPhase.DISTRIBUTION:
            return AdvancedGateResult(
                False,
                f"reject:amd_not_distribution:{amd.phase.value}",
                parent_amd_id=amd.amd_id,
                fbos_seen=fbos_seen,
            )
        if amd.bias != trade_dir:
            return AdvancedGateResult(
                False,
                f"reject:amd_bias_mismatch:{amd.bias}",
                parent_amd_id=amd.amd_id,
                fbos_seen=fbos_seen,
            )
        # POI family must still be valid at entry (not invalidated earlier same bar issues:
        # invalidation at bar B means bar B and later cannot use old POIs)
        crown_ok = False
        for p in tl.pois:
            if p.parent_amd_id != amd.amd_id:
                continue
            if p.invalidated_at is not None and p.invalidated_at <= entry_idx:
                continue
            if p.created_bar <= entry_idx:
                crown_ok = True
                break
        if not crown_ok:
            return AdvancedGateResult(
                False,
                "reject:no_valid_owned_poi",
                parent_amd_id=amd.amd_id,
                cisd_bar=cisd.bar if cisd else None,
                fbos_seen=fbos_seen,
            )
        tag = "mitigation" if entry_model == "mitigation" else "cisd_rbos"
        return AdvancedGateResult(
            True,
            f"accept:{tag}_parent_amd",
            parent_amd_id=amd.amd_id,
            cisd_bar=cisd.bar if cisd else None,
            fbos_seen=fbos_seen,
        )

    tag = "mitigation" if entry_model == "mitigation" else "cisd_rbos"
    return AdvancedGateResult(
        True,
        f"accept:{tag}",
        cisd_bar=cisd.bar if cisd else None,
        fbos_seen=fbos_seen,
    )
