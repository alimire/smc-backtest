#!/usr/bin/env python3
"""Read-only cTrader demo preflight. It never sends an order message."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from twisted.internet import reactor

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from executor_core import (  # noqa: E402
    AccountSnapshot,
    ExecutionPolicy,
    InstrumentSpec,
    TradeIntent,
    evaluate_trade,
    normalize_symbol,
    resolve_pip_value,
)

CRED_PATH = ROOT / "credentials" / "demo.json"
STATE_PATH = ROOT / ".executor_state.json"
LOG_PATH = ROOT / "reports" / "demo_executor.jsonl"
POLICY = ExecutionPolicy()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dry-run a safeguarded EURUSD trade against the Fusion demo account"
    )
    parser.add_argument("--setup-id", required=True, help="Unique scanner/alert setup ID")
    parser.add_argument("--side", required=True, choices=("long", "short", "buy", "sell"))
    parser.add_argument("--entry", required=True, type=float)
    parser.add_argument("--sl", required=True, type=float, help="Mandatory stop-loss price")
    parser.add_argument("--tp", required=True, type=float, help="Mandatory take-profit price")
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--risk", type=float, default=0.25, help="Risk percent, capped at 0.25")
    parser.add_argument(
        "--aplus",
        action="store_true",
        help="Confirm the signal passed the scanner's A+ filters",
    )
    return parser.parse_args()


def load_credentials() -> dict:
    if not CRED_PATH.exists():
        raise RuntimeError(f"missing {CRED_PATH}")
    credentials = json.loads(CRED_PATH.read_text())
    required = ("client_id", "client_secret", "access_token", "account_id")
    missing = [key for key in required if not credentials.get(key)]
    if missing:
        raise RuntimeError(f"missing credential fields: {', '.join(missing)}")
    return credentials


def load_seen_setup_ids() -> set[str]:
    if not STATE_PATH.exists():
        return set()
    try:
        data = json.loads(STATE_PATH.read_text())
        return set(data.get("approved_dry_runs", []))
    except (json.JSONDecodeError, OSError):
        return set()


def remember_setup(setup_id: str) -> None:
    seen = load_seen_setup_ids()
    seen.add(setup_id)
    STATE_PATH.write_text(
        json.dumps({"approved_dry_runs": sorted(seen)[-1000:]}, indent=2)
    )


def append_log(payload: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def uk_day_bounds_ms() -> tuple[int, int]:
    now = datetime.now(timezone.utc)
    local_now = now.astimezone(ZoneInfo("Europe/London"))
    local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(local_start.astimezone(timezone.utc).timestamp() * 1000), int(
        now.timestamp() * 1000
    )


def closed_deal_pnl(deals) -> float:
    total = 0.0
    for deal in deals:
        if not deal.HasField("closePositionDetail"):
            continue
        detail = deal.closePositionDetail
        digits = (
            getattr(detail, "moneyDigits", 0)
            or getattr(deal, "moneyDigits", 0)
            or 2
        )
        scale = 10 ** digits
        total += (
            detail.grossProfit
            + detail.swap
            + detail.commission
            + getattr(detail, "pnlConversionFee", 0)
        ) / scale
    return total


def fetch_snapshot(credentials: dict, symbol: str = "EURUSD") -> tuple[AccountSnapshot, InstrumentSpec]:
    from ctrader_open_api import Client, EndPoints, Protobuf, TcpProtocol
    from ctrader_open_api.messages.OpenApiMessages_pb2 import (
        ProtoOAAccountAuthReq,
        ProtoOAAccountAuthRes,
        ProtoOAApplicationAuthReq,
        ProtoOAApplicationAuthRes,
        ProtoOADealListReq,
        ProtoOADealListRes,
        ProtoOAErrorRes,
        ProtoOAGetAccountListByAccessTokenReq,
        ProtoOAGetAccountListByAccessTokenRes,
        ProtoOAReconcileReq,
        ProtoOAReconcileRes,
        ProtoOASymbolByIdReq,
        ProtoOASymbolByIdRes,
        ProtoOASymbolsListReq,
        ProtoOASymbolsListRes,
        ProtoOATraderReq,
        ProtoOATraderRes,
    )

    account_id = int(credentials["account_id"])
    target = normalize_symbol(symbol)
    host_name = str(credentials.get("host", "")).lower()
    if host_name != "demo":
        raise RuntimeError("credentials host is not demo; connection blocked")
    if account_id != POLICY.allowed_account_id:
        raise RuntimeError("credentials account_id is not allowlisted")

    client = Client(
        EndPoints.PROTOBUF_DEMO_HOST, EndPoints.PROTOBUF_PORT, TcpProtocol
    )
    result: dict = {}
    connected = False
    finished = False

    def finish(error: str | None = None) -> None:
        nonlocal finished
        if finished:
            return
        finished = True
        if error:
            result["error"] = error
        client.stopService()
        if reactor.running:
            reactor.callLater(0.1, reactor.stop)

    def send(request) -> None:
        client.send(request).addErrback(lambda failure: finish(str(failure.value)))

    def on_message(_client, message) -> None:
        payload_type = message.payloadType
        if payload_type == ProtoOAErrorRes().payloadType:
            error = Protobuf.extract(message)
            finish(f"{error.errorCode}: {getattr(error, 'description', '')}")
            return
        if payload_type == ProtoOAApplicationAuthRes().payloadType:
            request = ProtoOAGetAccountListByAccessTokenReq()
            request.accessToken = credentials["access_token"]
            send(request)
            return
        if payload_type == ProtoOAGetAccountListByAccessTokenRes().payloadType:
            response = Protobuf.extract(message)
            accounts = list(getattr(response, "ctidTraderAccount", []))
            match = next(
                (
                    account
                    for account in accounts
                    if int(account.ctidTraderAccountId) == account_id
                ),
                None,
            )
            if match is None:
                finish("allowlisted demo account is not linked to this token")
                return
            if match.isLive:
                finish("linked account is live; only Fusion demo is allowed")
                return
            if int(getattr(match, "traderLogin", 0) or 0) not in {
                0,
                POLICY.allowed_trader_login,
            }:
                finish("linked trader login is not Fusion demo 10123191")
                return
            result["is_live"] = bool(match.isLive)
            request = ProtoOAAccountAuthReq()
            request.ctidTraderAccountId = account_id
            request.accessToken = credentials["access_token"]
            send(request)
            return
        if payload_type == ProtoOAAccountAuthRes().payloadType:
            request = ProtoOATraderReq()
            request.ctidTraderAccountId = account_id
            send(request)
            return
        if payload_type == ProtoOATraderRes().payloadType:
            trader = Protobuf.extract(message).trader
            digits = getattr(trader, "moneyDigits", 2)
            result["balance"] = trader.balance / (10 ** digits)
            result["trader_login"] = int(trader.traderLogin)
            result["broker_name"] = getattr(trader, "brokerName", "") or ""
            request = ProtoOAReconcileReq()
            request.ctidTraderAccountId = account_id
            send(request)
            return
        if payload_type == ProtoOAReconcileRes().payloadType:
            response = Protobuf.extract(message)
            result["open_positions"] = len(response.position)
            result["pending_orders"] = len(response.order)
            result["_positions"] = list(response.position)
            start_ms, end_ms = uk_day_bounds_ms()
            request = ProtoOADealListReq()
            request.ctidTraderAccountId = account_id
            request.fromTimestamp = start_ms
            request.toTimestamp = end_ms
            request.maxRows = 1000
            send(request)
            return
        if payload_type == ProtoOADealListRes().payloadType:
            response = Protobuf.extract(message)
            result["daily_realized_pnl"] = closed_deal_pnl(response.deal)
            result["deal_history_complete"] = not response.hasMore
            request = ProtoOASymbolsListReq()
            request.ctidTraderAccountId = account_id
            send(request)
            return
        if payload_type == ProtoOASymbolsListRes().payloadType:
            response = Protobuf.extract(message)
            match = next(
                (
                    symbol
                    for symbol in response.symbol
                    if symbol.symbolName.upper().replace("/", "") == target
                ),
                None,
            )
            if match is None:
                finish(f"{target} is unavailable on this account")
                return
            result["symbol_name"] = match.symbolName
            result["symbol_id"] = match.symbolId
            request = ProtoOASymbolByIdReq()
            request.ctidTraderAccountId = account_id
            request.symbolId.append(match.symbolId)
            send(request)
            return
        if payload_type == ProtoOASymbolByIdRes().payloadType:
            response = Protobuf.extract(message)
            if not response.symbol:
                finish(f"{target} symbol specification was not returned")
                return
            symbol = response.symbol[0]
            pip_size = 10 ** (-symbol.pipPosition)
            lot_size = int(symbol.lotSize)
            pip_value = resolve_pip_value(target, lot_size, pip_size)
            result["instrument"] = InstrumentSpec(
                symbol=normalize_symbol(result["symbol_name"]),
                pip_size=pip_size,
                pip_value_per_lot=pip_value,
                lot_size_cents=lot_size,
                min_volume_cents=int(symbol.minVolume),
                step_volume_cents=int(symbol.stepVolume),
                pip_position=int(symbol.pipPosition),
            )
            sid = int(result["symbol_id"])
            same = [
                p for p in result.get("_positions", [])
                if int(p.tradeData.symbolId) == sid
            ]
            result["open_positions_for_symbol"] = len(same)
            finish()

    def on_connected(_client) -> None:
        nonlocal connected
        if connected:
            return
        connected = True
        request = ProtoOAApplicationAuthReq()
        request.clientId = credentials["client_id"]
        request.clientSecret = credentials["client_secret"]
        send(request)

    client.setConnectedCallback(on_connected)
    client.setMessageReceivedCallback(on_message)
    client.startService()
    reactor.callLater(20, lambda: finish("cTrader snapshot timed out"))
    reactor.run()

    if result.get("error"):
        raise RuntimeError(result["error"])
    required = (
        "balance",
        "trader_login",
        "open_positions",
        "pending_orders",
        "instrument",
        "is_live",
        "broker_name",
    )
    missing = [field for field in required if field not in result]
    if missing:
        raise RuntimeError(f"incomplete cTrader snapshot: {', '.join(missing)}")
    snapshot = AccountSnapshot(
        host=host_name,
        ctid_account_id=account_id,
        trader_login=result["trader_login"],
        balance=result["balance"],
        open_positions=result["open_positions"],
        pending_orders=result["pending_orders"],
        daily_realized_pnl=result.get("daily_realized_pnl", 0.0),
        deal_history_complete=result.get("deal_history_complete", False),
        is_live=result["is_live"],
        broker_name=result["broker_name"],
        open_positions_for_symbol=int(result.get("open_positions_for_symbol", 0)),
    )
    return snapshot, result["instrument"]


def main() -> int:
    args = parse_args()
    intent = TradeIntent(
        setup_id=args.setup_id,
        symbol=args.symbol,
        side=args.side,
        entry=args.entry,
        stop_loss=args.sl,
        take_profit=args.tp,
        risk_percent=args.risk,
        is_aplus=args.aplus,
    )

    print("DRY RUN ONLY - this program has no order-placement path.")
    try:
        snapshot, instrument = fetch_snapshot(load_credentials(), symbol=args.symbol)
    except Exception as exc:
        print(f"BLOCKED: could not verify demo account state: {exc}")
        return 2

    decision = evaluate_trade(
        intent,
        snapshot,
        seen_setup_ids=load_seen_setup_ids(),
        policy=POLICY,
        instrument=instrument,
    )
    log_record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": "dry-run",
        "intent": asdict(intent),
        "account": asdict(snapshot),
        "instrument": asdict(instrument),
        "decision": asdict(decision),
    }
    append_log(log_record)

    print(
        f"Account {snapshot.trader_login} | {snapshot.broker_name} | "
        f"balance ${snapshot.balance:.2f} | open {snapshot.open_positions} (sym={snapshot.open_positions_for_symbol}) | "
        f"pending orders {snapshot.pending_orders} | today P/L ${snapshot.daily_realized_pnl:.2f}"
    )
    if not decision.allowed:
        print("BLOCKED:")
        for reason in decision.reasons:
            print(f"  - {reason}")
        return 2

    remember_setup(intent.setup_id)
    print(
        f"APPROVED DRY RUN: {decision.normalized_side.upper()} {decision.normalized_symbol} | "
        f"risk ${decision.risk_amount:.2f} | {decision.stop_distance_pips:.1f} pips | "
        f"RR {decision.risk_reward:.2f} | {decision.lots:.2f} lots "
        f"({decision.units} units)"
    )
    print(
        f"Daily loss used ${decision.daily_loss_used:.2f} / "
        f"${decision.daily_loss_limit:.2f}. No order was placed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
