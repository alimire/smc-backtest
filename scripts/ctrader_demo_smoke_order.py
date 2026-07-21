#!/usr/bin/env python3
"""Place one tiny Fusion Markets DEMO market order with mandatory SL/TP.

Gated by --i-confirm-demo-order. DEMO host + allowlisted Fusion account only.
Supports EURUSD and XAUUSD. Gold defaults: 50/100 pips of 0.1 (not FX 15/30).
One open position per symbol — other symbols may already be open.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from twisted.internet import reactor

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from executor_core import (  # noqa: E402
    ExecutionPolicy,
    default_instrument,
    normalize_symbol,
    relative_distance_from_pips,
)

CRED_PATH = ROOT / "credentials" / "demo.json"
LOG_PATH = ROOT / "reports" / "demo_smoke_orders.jsonl"
POLICY = ExecutionPolicy()

SMOKE_DEFAULTS = {
    "EURUSD": {"sl_pips": 15.0, "tp_pips": 30.0},
    "GBPUSD": {"sl_pips": 15.0, "tp_pips": 30.0},
    "AUDUSD": {"sl_pips": 15.0, "tp_pips": 30.0},
    "USDCAD": {"sl_pips": 15.0, "tp_pips": 30.0},
    "NZDUSD": {"sl_pips": 15.0, "tp_pips": 30.0},
    "USDCHF": {"sl_pips": 15.0, "tp_pips": 30.0},
    "USDJPY": {"sl_pips": 15.0, "tp_pips": 30.0},  # pip=0.01
    "XAUUSD": {"sl_pips": 50.0, "tp_pips": 100.0},  # pip=0.1
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="One tiny Fusion demo smoke order")
    parser.add_argument("--i-confirm-demo-order", action="store_true")
    parser.add_argument("--side", choices=("buy", "sell"), default="buy")
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--label", default="")
    parser.add_argument("--sl-pips", type=float, default=None)
    parser.add_argument("--tp-pips", type=float, default=None)
    return parser.parse_args()


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


def append_log(payload: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def place_smoke_order(
    side: str,
    sl_pips: float,
    tp_pips: float,
    symbol: str,
    label: str,
) -> dict:
    from ctrader_open_api import Client, EndPoints, Protobuf, TcpProtocol
    from ctrader_open_api.messages.OpenApiMessages_pb2 import (
        ProtoOAAccountAuthReq,
        ProtoOAAccountAuthRes,
        ProtoOAAmendPositionSLTPReq,
        ProtoOAApplicationAuthReq,
        ProtoOAApplicationAuthRes,
        ProtoOAErrorRes,
        ProtoOAExecutionEvent,
        ProtoOAGetAccountListByAccessTokenReq,
        ProtoOAGetAccountListByAccessTokenRes,
        ProtoOANewOrderReq,
        ProtoOAOrderErrorEvent,
        ProtoOAReconcileReq,
        ProtoOAReconcileRes,
        ProtoOASpotEvent,
        ProtoOASubscribeSpotsReq,
        ProtoOASubscribeSpotsRes,
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
    target = normalize_symbol(symbol)
    client = Client(EndPoints.PROTOBUF_DEMO_HOST, EndPoints.PROTOBUF_PORT, TcpProtocol)
    result: dict = {"events": [], "symbol": target}
    # phase: auth → symbols → details → flat_check → spots → done
    phase = "auth"
    connected = False
    finished = False
    order_sent = False

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
        volume = result.get("min_volume")
        digits = int(result.get("digits") or 5)
        pip_position = int(result.get("pip_position") or 4)
        if None in (symbol_id, volume):
            finish("missing symbol metadata for order")
            return
        if sl_pips <= 0 or tp_pips <= 0 or tp_pips < 2 * sl_pips * 0.9:
            finish("smoke order requires positive SL and TP with at least ~2R")
            return

        relative_sl = relative_distance_from_pips(sl_pips, pip_position)
        relative_tp = relative_distance_from_pips(tp_pips, pip_position)
        is_buy = side == "buy"
        result["planned"] = {
            "side": side,
            "symbol": target,
            "relative_stop_loss": relative_sl,
            "relative_take_profit": relative_tp,
            "volume": volume,
            "sl_pips": sl_pips,
            "tp_pips": tp_pips,
            "bid": result.get("bid"),
            "ask": result.get("ask"),
            "label": label,
        }

        request = ProtoOANewOrderReq()
        request.ctidTraderAccountId = account_id
        request.symbolId = int(symbol_id)
        request.orderType = ProtoOAOrderType.MARKET
        request.tradeSide = ProtoOATradeSide.BUY if is_buy else ProtoOATradeSide.SELL
        request.volume = int(volume)
        request.relativeStopLoss = relative_sl
        request.relativeTakeProfit = relative_tp
        request.label = label[:50]
        request.comment = f"demo smoke {target}"
        order_sent = True
        quote = result.get("ask") if is_buy else result.get("bid")
        quote_txt = f"{quote:.{digits}f}" if quote else "market"
        print(
            f"Sending DEMO MARKET {side.upper()} {target} volume={volume} "
            f"quote~{quote_txt} relativeSL={relative_sl} relativeTP={relative_tp} "
            f"label={label}"
        )
        send(request)

    def on_message(_client, message) -> None:
        nonlocal phase
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
            money_digits = getattr(trader, "moneyDigits", 2)
            result["balance"] = trader.balance / (10 ** money_digits)
            result["broker"] = trader.brokerName
            phase = "symbols"
            request = ProtoOASymbolsListReq()
            request.ctidTraderAccountId = account_id
            send(request)
            return
        if payload_type == ProtoOASymbolsListRes().payloadType:
            response = Protobuf.extract(message)
            match = next(
                (
                    sym
                    for sym in response.symbol
                    if sym.symbolName.upper().replace("/", "") == target
                ),
                None,
            )
            if match is None:
                finish(f"{target} unavailable")
                return
            result["symbol_id"] = match.symbolId
            result["symbol_name"] = match.symbolName
            phase = "details"
            request = ProtoOASymbolByIdReq()
            request.ctidTraderAccountId = account_id
            request.symbolId.append(match.symbolId)
            send(request)
            return
        if payload_type == ProtoOASymbolByIdRes().payloadType:
            response = Protobuf.extract(message)
            if not response.symbol:
                finish(f"{target} symbol details missing")
                return
            sym = response.symbol[0]
            result["digits"] = int(sym.digits)
            result["pip_position"] = int(sym.pipPosition)
            result["pip_size"] = 10 ** (-sym.pipPosition)
            result["min_volume"] = int(sym.minVolume)
            phase = "flat_check"
            request = ProtoOAReconcileReq()
            request.ctidTraderAccountId = account_id
            send(request)
            return
        if payload_type == ProtoOAReconcileRes().payloadType:
            response = Protobuf.extract(message)
            if order_sent:
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
                            "label": getattr(trade, "label", ""),
                        }
                    )
                result["positions"] = positions
                finish()
                return
            if phase != "flat_check":
                return
            symbol_id = int(result["symbol_id"])
            same_pos = [
                p for p in response.position if int(p.tradeData.symbolId) == symbol_id
            ]
            same_ord = [
                o for o in response.order if int(o.tradeData.symbolId) == symbol_id
            ]
            if same_pos or same_ord:
                finish(f"{target} already has open positions or pending orders")
                return
            phase = "spots"
            request = ProtoOASubscribeSpotsReq()
            request.ctidTraderAccountId = account_id
            request.symbolId.append(symbol_id)
            send(request)
            reactor.callLater(2.0, send_order)
            return
        if payload_type == ProtoOASubscribeSpotsRes().payloadType:
            print(f"Subscribed to {target} spots")
            return
        if payload_type == ProtoOASpotEvent().payloadType:
            spot = Protobuf.extract(message)
            if spot.symbolId != result.get("symbol_id"):
                return

            def normalize_price(raw: float) -> float:
                digits = int(result.get("digits") or 5)
                if raw >= 100:
                    return raw / (10 ** digits)
                return raw

            if spot.HasField("bid"):
                result["bid"] = normalize_price(spot.bid)
            if spot.HasField("ask"):
                result["ask"] = normalize_price(spot.ask)
            if result.get("bid") and result.get("ask") and not order_sent:
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
                    pip = float(result.get("pip_size") or 0.0001)
                    is_buy = side == "buy"
                    stop = round(
                        entry - sl_pips * pip if is_buy else entry + sl_pips * pip,
                        digits,
                    )
                    take = round(
                        entry + tp_pips * pip if is_buy else entry - tp_pips * pip,
                        digits,
                    )
                    result["filled_position"] = {
                        "positionId": position.positionId,
                        "entry": entry,
                        "stopLoss": stop,
                        "takeProfit": take,
                        "volume": position.tradeData.volume,
                        "symbol": target,
                        "label": label,
                    }
                    if not getattr(position, "stopLoss", 0) or not getattr(
                        position, "takeProfit", 0
                    ):
                        amend = ProtoOAAmendPositionSLTPReq()
                        amend.ctidTraderAccountId = account_id
                        amend.positionId = position.positionId
                        amend.stopLoss = stop
                        amend.takeProfit = take
                        print(
                            f"Attaching SL={stop} TP={take} to position {position.positionId}"
                        )
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
    reactor.callLater(30, lambda: finish("smoke order timed out"))
    reactor.run()
    return result


def main() -> int:
    args = parse_args()
    if not args.i_confirm_demo_order:
        print("Refusing: pass --i-confirm-demo-order to place a real DEMO trade.")
        return 2

    symbol = normalize_symbol(args.symbol)
    allowed = set(POLICY.allowed_symbols) | {normalize_symbol(POLICY.allowed_symbol)}
    if symbol not in allowed:
        print(f"Refusing: symbol must be one of {sorted(allowed)}")
        return 2

    defaults = SMOKE_DEFAULTS.get(symbol, SMOKE_DEFAULTS["EURUSD"])
    sl_pips = float(args.sl_pips if args.sl_pips is not None else defaults["sl_pips"])
    tp_pips = float(args.tp_pips if args.tp_pips is not None else defaults["tp_pips"])
    if sl_pips <= 0 or tp_pips <= 0:
        print("Refusing: SL and TP pips must be positive.")
        return 2

    label = (
        args.label or ("smc-smoke-gold" if symbol == "XAUUSD" else "smc-smoke")
    ).strip()
    inst = default_instrument(symbol)

    print(
        f"DEMO ONLY smoke order — Fusion Markets 10123191 / {symbol} "
        f"minimum size (pip={inst.pip_size}, SL={sl_pips} TP={tp_pips} pips, label={label})."
    )
    try:
        result = place_smoke_order(args.side, sl_pips, tp_pips, symbol, label)
    except Exception as exc:
        print(f"FAILED: {exc}")
        return 1

    append_log(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "side": args.side,
            "symbol": symbol,
            "label": label,
            "sl_pips": sl_pips,
            "tp_pips": tp_pips,
            "result": {
                key: value for key, value in result.items() if key != "events" or value
            },
        }
    )

    filled = result.get("filled_position") or (result.get("positions") or [None])[0]
    if filled:
        # Fill wins over a late reactor timeout after ORDER_FILLED.
        result.pop("error", None)
    elif result.get("error"):
        print(f"FAILED: {result['error']}")
        return 1
    else:
        print("FAILED: no filled position returned")
        return 1

    print("ORDER FILLED on DEMO")
    print(f"  broker: {result.get('broker')}")
    print(f"  symbol: {symbol}")
    print(f"  label: {label}")
    print(f"  balance before: ${result.get('balance', 0):.2f}")
    print(f"  positionId: {filled.get('positionId')}")
    print(f"  entry: {filled.get('entry')}")
    print(f"  stopLoss: {filled.get('stopLoss')}")
    print(f"  takeProfit: {filled.get('takeProfit')}")
    print(f"  volume: {filled.get('volume')}")
    print(
        f"Open cTrader demo → Positions → look for {symbol} labeled '{label}' "
        f"(min volume, protected SL/TP)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
