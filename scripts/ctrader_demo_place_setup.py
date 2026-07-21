#!/usr/bin/env python3
"""Place one Fusion Markets DEMO market order for an approved SMC setup.

Hard safety:
  - DEMO=1 required in the environment
  - credentials host must be demo
  - allowlisted Fusion demo account only
  - dry-run preflight via ctrader_demo_executor.py (A+, RR, risk, flat, daily loss)
  - never targets live hosts
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from twisted.internet import reactor

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from executor_core import ExecutionPolicy, InstrumentSpec, TradeIntent, normalize_symbol  # noqa: E402

CRED_PATH = ROOT / "credentials" / "demo.json"
STATE_PATH = ROOT / ".executor_state.json"
LOG_PATH = ROOT / "reports" / "demo_live_bot.jsonl"
DRY_LOG_PATH = ROOT / "reports" / "demo_executor.jsonl"
POLICY = ExecutionPolicy()


def require_demo_env() -> None:
    if os.environ.get("DEMO", "").strip() != "1":
        raise RuntimeError("Refusing: set DEMO=1 (demo-only hard gate)")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Place one DEMO order for an SMC setup")
    p.add_argument("--setup-id", required=True)
    p.add_argument("--side", required=True, choices=("long", "short", "buy", "sell"))
    p.add_argument("--entry", required=True, type=float)
    p.add_argument("--sl", required=True, type=float)
    p.add_argument("--tp", required=True, type=float)
    p.add_argument("--risk", type=float, default=0.25)
    p.add_argument("--symbol", default="EURUSD", help="EURUSD or XAUUSD")
    p.add_argument(
        "--i-confirm-demo-order",
        action="store_true",
        help="Required confirmation that a real DEMO order may be sent",
    )
    return p.parse_args()


def load_credentials() -> dict:
    credentials = json.loads(CRED_PATH.read_text())
    if str(credentials.get("host", "")).lower() != "demo":
        raise RuntimeError("host must be demo")
    if int(credentials.get("account_id") or 0) != POLICY.allowed_account_id:
        raise RuntimeError("account_id is not allowlisted Fusion demo")
    for key in ("client_id", "client_secret", "access_token"):
        if not credentials.get(key):
            raise RuntimeError(f"missing {key}")
    return credentials


def remember_setup(setup_id: str) -> None:
    seen_orders: set[str] = set()
    seen_dry: set[str] = set()
    if STATE_PATH.exists():
        try:
            data = json.loads(STATE_PATH.read_text())
            seen_orders = set(data.get("approved_orders", []))
            seen_dry = set(data.get("approved_dry_runs", []))
        except (json.JSONDecodeError, OSError):
            pass
    seen_orders.add(setup_id)
    STATE_PATH.write_text(
        json.dumps(
            {
                "approved_orders": sorted(seen_orders)[-1000:],
                "approved_dry_runs": sorted(seen_dry)[-1000:],
            },
            indent=2,
        )
        + "\n"
    )


def append_log(payload: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a") as handle:
        handle.write(json.dumps(payload, sort_keys=True, default=str) + "\n")


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


def place_demo_setup(intent: TradeIntent, decision_volume_cents: int) -> dict:
    from ctrader_open_api import Client, EndPoints, Protobuf, TcpProtocol
    from ctrader_open_api.messages.OpenApiMessages_pb2 import (
        ProtoOAAccountAuthReq,
        ProtoOAAccountAuthRes,
        ProtoOAAmendPositionSLTPReq,
        ProtoOAApplicationAuthReq,
        ProtoOAApplicationAuthRes,
        ProtoOADealListReq,
        ProtoOADealListRes,
        ProtoOAErrorRes,
        ProtoOAExecutionEvent,
        ProtoOAGetAccountListByAccessTokenReq,
        ProtoOAGetAccountListByAccessTokenRes,
        ProtoOANewOrderReq,
        ProtoOAOrderErrorEvent,
        ProtoOAReconcileReq,
        ProtoOAReconcileRes,
        ProtoOASymbolByIdReq,
        ProtoOASymbolByIdRes,
        ProtoOASymbolsListReq,
        ProtoOASymbolsListRes,
        ProtoOATraderReq,
        ProtoOATraderRes,
    )
    from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
        ProtoOAExecutionType,
        ProtoOAOrderType,
        ProtoOATradeSide,
    )

    credentials = load_credentials()
    account_id = POLICY.allowed_account_id
    client = Client(EndPoints.PROTOBUF_DEMO_HOST, EndPoints.PROTOBUF_PORT, TcpProtocol)
    result: dict = {"events": [], "snapshot": {}}
    connected = False
    finished = False
    order_sent = False
    side = intent.side.lower().strip()
    if side in ("buy", "bullish"):
        side = "long"
    elif side in ("sell", "bearish"):
        side = "short"
    is_buy = side == "long"

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

    def send(request):
        deferred = client.send(request)
        deferred.addErrback(lambda failure: finish(str(failure.value)))
        return deferred

    def send_order() -> None:
        nonlocal order_sent
        if order_sent:
            return
        symbol_id = result.get("symbol_id")
        digits = int(result.get("digits") or 5)
        if symbol_id is None:
            finish("missing symbol metadata for order")
            return
        volume = int(decision_volume_cents)
        if volume < int(result.get("min_volume") or 0):
            finish("volume below broker minimum")
            return

        request = ProtoOANewOrderReq()
        request.ctidTraderAccountId = account_id
        request.symbolId = symbol_id
        request.orderType = ProtoOAOrderType.MARKET
        request.tradeSide = ProtoOATradeSide.BUY if is_buy else ProtoOATradeSide.SELL
        request.volume = volume
        request.label = "smc-research-best"
        request.comment = intent.setup_id[:50]
        order_sent = True
        result["planned"] = {
            "side": side,
            "volume": volume,
            "entry": intent.entry,
            "stop_loss": intent.stop_loss,
            "take_profit": intent.take_profit,
        }
        print(
            f"Sending DEMO MARKET {side.upper()} {intent.symbol} volume={volume} "
            f"SL={intent.stop_loss:.{digits}f} TP={intent.take_profit:.{digits}f}"
        )
        send(request)

    def on_message(_client, message) -> None:
        payload_type = message.payloadType
        if payload_type == ProtoOAErrorRes().payloadType:
            error = Protobuf.extract(message)
            finish(f"{error.errorCode}: {getattr(error, 'description', '')}")
            return
        if payload_type == ProtoOAOrderErrorEvent().payloadType:
            error = Protobuf.extract(message)
            finish(
                f"order error {error.errorCode}: {getattr(error, 'description', '')}"
            )
            return
        if payload_type == ProtoOAApplicationAuthRes().payloadType:
            request = ProtoOAGetAccountListByAccessTokenReq()
            request.accessToken = credentials["access_token"]
            send(request)
            return
        if payload_type == ProtoOAGetAccountListByAccessTokenRes().payloadType:
            response = Protobuf.extract(message)
            match = next(
                (
                    account
                    for account in response.ctidTraderAccount
                    if int(account.ctidTraderAccountId) == account_id and not account.isLive
                ),
                None,
            )
            if match is None:
                finish("allowlisted Fusion demo account not linked")
                return
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
            if int(trader.traderLogin) != POLICY.allowed_trader_login:
                finish("trader login mismatch")
                return
            if "fusion" not in (getattr(trader, "brokerName", "") or "").lower():
                finish("broker is not Fusion Markets")
                return
            digits = getattr(trader, "moneyDigits", 2)
            result["snapshot"]["balance"] = trader.balance / (10 ** digits)
            result["snapshot"]["trader_login"] = int(trader.traderLogin)
            result["snapshot"]["broker_name"] = trader.brokerName
            request = ProtoOAReconcileReq()
            request.ctidTraderAccountId = account_id
            send(request)
            return
        if payload_type == ProtoOAReconcileRes().payloadType:
            response = Protobuf.extract(message)
            if not order_sent:
                result["snapshot"]["open_positions"] = len(response.position)
                result["snapshot"]["pending_orders"] = len(response.order)
                # One-per-symbol: allow other symbols to remain open.
                # Symbol id resolved later; count pending globally still blocked.
                if response.order:
                    finish("pending orders must be flat before a new setup")
                    return
                # Defer same-symbol position check until after symbol id is known
                result["snapshot"]["_positions"] = list(response.position)
                start_ms, end_ms = uk_day_bounds_ms()
                request = ProtoOADealListReq()
                request.ctidTraderAccountId = account_id
                request.fromTimestamp = start_ms
                request.toTimestamp = end_ms
                request.maxRows = 1000
                send(request)
                return
            positions = []
            for position in response.position:
                trade = position.tradeData
                positions.append(
                    {
                        "positionId": position.positionId,
                        "symbolId": trade.symbolId,
                        "volume": trade.volume,
                        "side": trade.tradeSide,
                        "entry": getattr(position, "price", None),
                        "stopLoss": getattr(position, "stopLoss", None),
                        "takeProfit": getattr(position, "takeProfit", None),
                    }
                )
            result["positions"] = positions
            finish()
            return
        if payload_type == ProtoOADealListRes().payloadType:
            response = Protobuf.extract(message)
            result["snapshot"]["daily_realized_pnl"] = closed_deal_pnl(response.deal)
            result["snapshot"]["deal_history_complete"] = not response.hasMore
            request = ProtoOASymbolsListReq()
            request.ctidTraderAccountId = account_id
            send(request)
            return
        if payload_type == ProtoOASymbolsListRes().payloadType:
            response = Protobuf.extract(message)
            target = intent.symbol.upper().replace("/", "")
            match = next(
                (
                    symbol
                    for symbol in response.symbol
                    if symbol.symbolName.upper().replace("/", "") == target
                ),
                None,
            )
            if match is None:
                finish(f"{target} unavailable")
                return
            result["symbol_id"] = match.symbolId
            request = ProtoOASymbolByIdReq()
            request.ctidTraderAccountId = account_id
            request.symbolId.append(match.symbolId)
            send(request)
            return
        if payload_type == ProtoOASymbolByIdRes().payloadType:
            response = Protobuf.extract(message)
            if not response.symbol:
                finish(f"{intent.symbol} symbol details missing")
                return
            symbol = response.symbol[0]
            result["digits"] = int(symbol.digits)
            result["min_volume"] = int(symbol.minVolume)
            sid = int(result["symbol_id"])
            same = [
                p for p in result.get("snapshot", {}).get("_positions", [])
                if int(p.tradeData.symbolId) == sid
            ]
            result["snapshot"]["open_positions_for_symbol"] = len(same)
            if same:
                finish(f"{intent.symbol} already has an open position")
                return
            send_order()
            return
        if payload_type == ProtoOAExecutionEvent().payloadType:
            event = Protobuf.extract(message)
            result["events"].append(
                {
                    "type": int(event.executionType),
                    "error": getattr(event, "errorCode", ""),
                }
            )
            if event.executionType == ProtoOAExecutionType.ORDER_REJECTED:
                finish(f"order rejected: {getattr(event, 'errorCode', '')}")
                return
            if event.executionType in (
                ProtoOAExecutionType.ORDER_FILLED,
                ProtoOAExecutionType.ORDER_PARTIAL_FILL,
            ):
                if event.HasField("position"):
                    position = event.position
                    entry = float(getattr(position, "price", 0) or 0)
                    digits = int(result.get("digits") or 5)
                    stop = round(float(intent.stop_loss), digits)
                    take = round(float(intent.take_profit), digits)
                    result["filled_position"] = {
                        "positionId": position.positionId,
                        "entry": entry,
                        "stopLoss": stop,
                        "takeProfit": take,
                        "volume": position.tradeData.volume,
                    }
                    if not getattr(position, "stopLoss", 0) or not getattr(
                        position, "takeProfit", 0
                    ):
                        amend = ProtoOAAmendPositionSLTPReq()
                        amend.ctidTraderAccountId = account_id
                        amend.positionId = position.positionId
                        amend.stopLoss = stop
                        amend.takeProfit = take
                        print(f"Attaching SL={stop} TP={take} to position {position.positionId}")
                        send(amend)
                        return
                request = ProtoOAReconcileReq()
                request.ctidTraderAccountId = account_id
                send(request)

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
    reactor.callLater(30, lambda: finish("demo place-setup timed out"))
    reactor.run()
    return result


def run_preflight(intent: TradeIntent) -> tuple[int, int]:
    """Return (returncode, broker_volume_cents)."""
    dry_cmd = [
        sys.executable,
        str(ROOT / "scripts" / "ctrader_demo_executor.py"),
        "--setup-id",
        intent.setup_id,
        "--symbol",
        intent.symbol,
        "--side",
        intent.side,
        "--entry",
        str(intent.entry),
        "--sl",
        str(intent.stop_loss),
        "--tp",
        str(intent.take_profit),
        "--risk",
        str(intent.risk_percent),
        "--aplus",
    ]
    dry_proc = subprocess.run(dry_cmd, cwd=str(ROOT), capture_output=True, text=True)
    if dry_proc.stdout:
        print(dry_proc.stdout.rstrip())
    if dry_proc.stderr:
        print(dry_proc.stderr.rstrip(), file=sys.stderr)

    from executor_core import default_instrument
    volume = default_instrument(intent.symbol).min_volume_cents
    if DRY_LOG_PATH.exists():
        try:
            last = json.loads(DRY_LOG_PATH.read_text().strip().splitlines()[-1])
            volume = int(
                last.get("decision", {}).get("broker_volume_cents")
                or volume
            )
        except (json.JSONDecodeError, OSError, IndexError, TypeError, ValueError):
            pass
    return dry_proc.returncode, volume


def main() -> int:
    require_demo_env()
    args = parse_args()
    if not args.i_confirm_demo_order:
        print("Refusing: pass --i-confirm-demo-order to place a real DEMO trade.")
        return 2

    intent = TradeIntent(
        setup_id=args.setup_id,
        symbol=normalize_symbol(args.symbol),
        side=args.side,
        entry=args.entry,
        stop_loss=args.sl,
        take_profit=args.tp,
        risk_percent=min(args.risk, POLICY.max_risk_percent),
        is_aplus=True,
    )

    print("DEMO ONLY — Fusion 10123191 / research_best setup order.")
    rc, volume = run_preflight(intent)
    if rc != 0:
        append_log(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": "blocked_preflight",
                "setup_id": intent.setup_id,
                "returncode": rc,
            }
        )
        print("BLOCKED by demo preflight — no order sent.")
        return 2

    try:
        result = place_demo_setup(intent, volume)
    except Exception as exc:
        append_log(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": "error",
                "setup_id": intent.setup_id,
                "error": str(exc),
            }
        )
        print(f"FAILED: {exc}")
        return 1

    append_log(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "order_attempt",
            "mode": "demo",
            "intent": asdict(intent),
            "volume_cents": volume,
            "result": {k: v for k, v in result.items() if k != "events" or v},
        }
    )

    if result.get("error"):
        print(f"FAILED: {result['error']}")
        return 1

    filled = result.get("filled_position") or (result.get("positions") or [None])[0]
    if not filled:
        print("FAILED: no filled position returned")
        return 1

    remember_setup(intent.setup_id)
    print("ORDER FILLED on DEMO")
    print(f"  positionId: {filled.get('positionId')}")
    print(f"  entry: {filled.get('entry')}")
    print(f"  stopLoss: {filled.get('stopLoss')}")
    print(f"  takeProfit: {filled.get('takeProfit')}")
    print(f"  volume: {filled.get('volume')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
