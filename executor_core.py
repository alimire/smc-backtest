"""Pure safety policy and position sizing for the cTrader demo executor."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Collection, Mapping


# FX majors added after the Jul 2026 multi-symbol research_best OOS study
# (see docs/RESEARCH_RESULTS.md). These trade broker MINIMUM volume only.
# USDX (US Dollar Index, Fusion id 120) added Jul 2026 after passing the same
# OOS bar (n=38, E[R]=6.30, PF=9.55).
FX_MIN_VOLUME_SYMBOLS: frozenset[str] = frozenset({
    "GBPUSD",
    "USDJPY",
    "AUDUSD",
    "USDCAD",
    "NZDUSD",
    "USDCHF",
    "USDX",
})

ALLOWED_SYMBOLS = frozenset({"EURUSD", "XAUUSD"}) | FX_MIN_VOLUME_SYMBOLS


@dataclass(frozen=True)
class TradeIntent:
    setup_id: str
    symbol: str
    side: str
    entry: float
    stop_loss: float
    take_profit: float
    risk_percent: float = 0.25
    is_aplus: bool = True
    # "A+" = full research_best rules; "A" = one documented relaxation
    # (mid 0.45/0.55). A-tier orders always trade broker minimum volume.
    tier: str = "A+"


@dataclass(frozen=True)
class AccountSnapshot:
    host: str
    ctid_account_id: int
    trader_login: int
    balance: float
    open_positions: int
    pending_orders: int = 0
    daily_realized_pnl: float = 0.0
    deal_history_complete: bool = True
    is_live: bool = False
    broker_name: str = "Fusion Markets"
    # Positions already open on the intent's symbol (one-per-symbol policy).
    open_positions_for_symbol: int = 0


@dataclass(frozen=True)
class InstrumentSpec:
    symbol: str = "EURUSD"
    pip_size: float = 0.0001
    pip_value_per_lot: float = 10.0
    lot_size_cents: int = 10_000_000
    min_volume_cents: int = 100_000
    step_volume_cents: int = 100_000
    pip_position: int = 4


@dataclass(frozen=True)
class ExecutionPolicy:
    allowed_account_id: int = 47_820_966
    allowed_trader_login: int = 10_123_191
    allowed_symbols: frozenset[str] = field(default_factory=lambda: ALLOWED_SYMBOLS)
    # Back-compat alias used by older callers / docs.
    allowed_symbol: str = "EURUSD"
    allowed_broker_substring: str = "Fusion"
    max_risk_percent: float = 0.25
    daily_loss_limit_percent: float = 1.0
    minimum_rr: float = 2.0
    # One open position *per symbol* (EURUSD and XAUUSD may both be open).
    max_open_positions_per_symbol: int = 1
    max_open_positions: int = 1  # legacy alias → per-symbol
    max_pending_orders: int = 0
    # Symbols that always trade broker minimum volume (new pairs, Jul 2026).
    min_volume_symbols: frozenset[str] = field(
        default_factory=lambda: FX_MIN_VOLUME_SYMBOLS
    )
    # Signal tiers allowed to trade (A added Jul 2026 after OOS PASS).
    allowed_tiers: frozenset[str] = field(
        default_factory=lambda: frozenset({"A+", "A"})
    )
    # Correlation guard: all 9 symbols are USD-correlated, so cap TOTAL
    # simultaneous open positions across the account (any tier).
    max_total_open_positions: int = 3


@dataclass(frozen=True)
class ExecutionDecision:
    allowed: bool
    reasons: tuple[str, ...]
    normalized_symbol: str
    normalized_side: str
    risk_amount: float = 0.0
    risk_reward: float = 0.0
    stop_distance_pips: float = 0.0
    lots: float = 0.0
    units: int = 0
    broker_volume_cents: int = 0
    daily_loss_used: float = 0.0
    daily_loss_limit: float = 0.0


def normalize_symbol(symbol: str) -> str:
    return symbol.upper().replace("/", "").removesuffix("=X").strip()


def normalize_side(side: str) -> str:
    value = side.lower().strip()
    if value in {"buy", "bullish"}:
        return "long"
    if value in {"sell", "bearish"}:
        return "short"
    return value


def default_instrument(symbol: str) -> InstrumentSpec:
    """Broker defaults for Fusion demo (verified via ProtoOASymbolById)."""
    sym = normalize_symbol(symbol)
    if sym == "XAUUSD":
        return InstrumentSpec(
            symbol="XAUUSD",
            pip_size=0.1,
            pip_value_per_lot=10.0,
            lot_size_cents=10_000,
            min_volume_cents=100,
            step_volume_cents=100,
            pip_position=1,
        )
    if sym.endswith("JPY"):
        # Fusion USDJPY: digits=3, pipPosition=2 → pip 0.01 (verified Jul 2026).
        return InstrumentSpec(
            symbol=sym,
            pip_size=0.01,
            pip_value_per_lot=10.0,
            lot_size_cents=10_000_000,
            min_volume_cents=100_000,
            step_volume_cents=100_000,
            pip_position=2,
        )
    if sym == "USDX":
        # Fusion US Dollar Index: pip 0.01, minVolume 100 (like gold).
        # Live broker specs override these at order time.
        return InstrumentSpec(
            symbol="USDX",
            pip_size=0.01,
            pip_value_per_lot=10.0,
            lot_size_cents=10_000,
            min_volume_cents=100,
            step_volume_cents=100,
            pip_position=2,
        )
    return InstrumentSpec(
        symbol=sym if sym in ALLOWED_SYMBOLS else "EURUSD",
        pip_size=0.0001,
        pip_value_per_lot=10.0,
        lot_size_cents=10_000_000,
        min_volume_cents=100_000,
        step_volume_cents=100_000,
        pip_position=4,
    )


def relative_distance_from_pips(pips: float, pip_position: int) -> int:
    """cTrader relative SL/TP units: 1/100000 of price = 10^(5 - pipPosition) per pip."""
    return int(round(float(pips) * (10 ** (5 - int(pip_position)))))


def evaluate_trade(
    intent: TradeIntent,
    snapshot: AccountSnapshot,
    seen_setup_ids: Collection[str] = (),
    policy: ExecutionPolicy = ExecutionPolicy(),
    instrument: InstrumentSpec = InstrumentSpec(),
) -> ExecutionDecision:
    """Validate a proposed trade and calculate a volume without placing an order."""
    reasons: list[str] = []
    symbol = normalize_symbol(intent.symbol)
    side = normalize_side(intent.side)
    prices = (intent.entry, intent.stop_loss, intent.take_profit)
    allowed = set(policy.allowed_symbols) | {normalize_symbol(policy.allowed_symbol)}

    if snapshot.host.lower() != "demo":
        reasons.append("live hosts are blocked")
    if snapshot.is_live:
        reasons.append("live accounts are blocked")
    if snapshot.ctid_account_id != policy.allowed_account_id:
        reasons.append("cTrader account ID is not allowlisted")
    if snapshot.trader_login != policy.allowed_trader_login:
        reasons.append("trader login is not Fusion demo 10123191")
    if policy.allowed_broker_substring.lower() not in snapshot.broker_name.lower():
        reasons.append("broker is not Fusion Markets")
    if symbol not in allowed or symbol != normalize_symbol(instrument.symbol):
        reasons.append(f"symbol not allowlisted (allowed: {', '.join(sorted(allowed))})")
    if side not in {"long", "short"}:
        reasons.append("side must be long or short")
    if not intent.setup_id.strip():
        reasons.append("setup_id is required")
    elif intent.setup_id in seen_setup_ids:
        reasons.append("duplicate setup_id")
    tier = (intent.tier or "A+").strip().upper()
    if tier not in policy.allowed_tiers:
        reasons.append(
            f"tier {tier} not allowed (allowed: {', '.join(sorted(policy.allowed_tiers))})"
        )
    if tier == "A+" and not intent.is_aplus:
        reasons.append("only A+ setups are allowed")
    if not all(math.isfinite(price) and price > 0 for price in prices):
        reasons.append("entry, stop_loss, and take_profit must be positive finite prices")
    if not math.isfinite(intent.risk_percent) or intent.risk_percent <= 0:
        reasons.append("risk_percent must be positive")
    elif intent.risk_percent > policy.max_risk_percent:
        reasons.append(f"risk_percent exceeds {policy.max_risk_percent:.2f}% cap")
    if not math.isfinite(snapshot.balance) or snapshot.balance <= 0:
        reasons.append("account balance must be positive")
    per_sym_limit = policy.max_open_positions_per_symbol or policy.max_open_positions
    open_for_symbol = snapshot.open_positions_for_symbol
    if open_for_symbol >= per_sym_limit:
        reasons.append("maximum open-position limit reached for symbol")
    if snapshot.open_positions >= policy.max_total_open_positions:
        reasons.append(
            f"correlation guard: max {policy.max_total_open_positions} total open positions reached"
        )
    if snapshot.pending_orders > policy.max_pending_orders:
        reasons.append("pending orders must be flat before a new setup")
    if not snapshot.deal_history_complete:
        reasons.append("daily deal history is incomplete")

    risk_distance = abs(intent.entry - intent.stop_loss)
    reward_distance = abs(intent.take_profit - intent.entry)
    if all(math.isfinite(price) and price > 0 for price in prices):
        if side == "long" and not (
            intent.stop_loss < intent.entry < intent.take_profit
        ):
            reasons.append("long requires stop_loss < entry < take_profit")
        if side == "short" and not (
            intent.take_profit < intent.entry < intent.stop_loss
        ):
            reasons.append("short requires take_profit < entry < stop_loss")
        if risk_distance <= 0:
            reasons.append("stop_loss must differ from entry")

    rr = reward_distance / risk_distance if risk_distance > 0 else 0.0
    if risk_distance > 0 and rr + 1e-9 < policy.minimum_rr:
        reasons.append(f"risk/reward is below {policy.minimum_rr:.1f}")

    day_start_balance = snapshot.balance - snapshot.daily_realized_pnl
    daily_limit = max(day_start_balance, 0.0) * policy.daily_loss_limit_percent / 100
    daily_used = -snapshot.daily_realized_pnl if snapshot.daily_realized_pnl < 0 else 0.0
    risk_amount = (
        snapshot.balance * intent.risk_percent / 100
        if snapshot.balance > 0 and intent.risk_percent > 0
        else 0.0
    )
    if daily_limit <= 0:
        reasons.append("daily loss limit could not be calculated")
    elif daily_used >= daily_limit:
        reasons.append("daily loss limit reached")
    elif daily_used + risk_amount > daily_limit:
        reasons.append("proposed risk would exceed the daily loss limit")

    stop_pips = risk_distance / instrument.pip_size if instrument.pip_size > 0 else 0.0
    raw_lots = (
        risk_amount / (stop_pips * instrument.pip_value_per_lot)
        if stop_pips > 0 and instrument.pip_value_per_lot > 0
        else 0.0
    )
    step = instrument.step_volume_cents
    broker_volume = (
        math.floor(raw_lots * instrument.lot_size_cents / step) * step
        if raw_lots > 0 and step > 0 and instrument.lot_size_cents > 0
        else 0
    )
    if symbol in policy.min_volume_symbols and instrument.min_volume_cents > 0:
        # New pairs deliberately trade the broker minimum volume only.
        broker_volume = instrument.min_volume_cents
    if tier == "A" and instrument.min_volume_cents > 0:
        # A tier never sizes up: broker minimum volume regardless of symbol.
        broker_volume = instrument.min_volume_cents
    if broker_volume < instrument.min_volume_cents:
        reasons.append("calculated volume is below the broker minimum")
    if instrument.step_volume_cents <= 0 or instrument.lot_size_cents <= 0:
        reasons.append("invalid broker volume specification")

    lots = (
        broker_volume / instrument.lot_size_cents
        if instrument.lot_size_cents > 0
        else 0.0
    )
    units = broker_volume // 100
    return ExecutionDecision(
        allowed=not reasons,
        reasons=tuple(reasons),
        normalized_symbol=symbol,
        normalized_side=side,
        risk_amount=round(risk_amount, 2),
        risk_reward=round(rr, 2),
        stop_distance_pips=round(stop_pips, 1),
        lots=round(lots, 4),
        units=units,
        broker_volume_cents=broker_volume,
        daily_loss_used=round(daily_used, 2),
        daily_loss_limit=round(daily_limit, 2),
    )


def pip_value_hint(symbol: str, lot_size_cents: int, pip_size: float) -> float:
    """Rough $ per pip per lot from broker lotSize (units = lot_size_cents/100)."""
    units_per_lot = lot_size_cents / 100.0
    return units_per_lot * pip_size


def resolve_pip_value(
    symbol: str,
    lot_size_cents: int,
    pip_size: float,
    overrides: Mapping[str, float] | None = None,
) -> float:
    if overrides and normalize_symbol(symbol) in overrides:
        return float(overrides[normalize_symbol(symbol)])
    hint = pip_value_hint(symbol, lot_size_cents, pip_size)
    # Fusion XAUUSD: 100 oz * $0.1 pip ≈ $10; EURUSD standard $10.
    return hint if hint > 0 else 10.0
