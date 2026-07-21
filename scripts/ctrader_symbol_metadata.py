#!/usr/bin/env python3
"""Read Fusion cTrader DEMO symbol metadata for an explicit symbol list."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from twisted.internet import reactor

ROOT = Path(__file__).resolve().parent.parent
CRED_PATH = ROOT / "credentials" / "demo.json"
ALLOWED_ACCOUNT_ID = 47_820_966
DEFAULT_SYMBOLS = (
    "EURUSD",
    "XAUUSD",
    "GBPUSD",
    "USDJPY",
    "AUDUSD",
    "USDCAD",
    "NZDUSD",
    "USDCHF",
    "USDX",
)


def normalize_symbol(value: str) -> str:
    return value.upper().replace("/", "").strip()


def load_credentials() -> dict:
    credentials = json.loads(CRED_PATH.read_text())
    if str(credentials.get("host", "")).lower() != "demo":
        raise RuntimeError("credentials host must be demo")
    if int(credentials.get("account_id") or 0) != ALLOWED_ACCOUNT_ID:
        raise RuntimeError(f"account_id must be {ALLOWED_ACCOUNT_ID}")
    return credentials


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbols", nargs="*", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--json-out", default="")
    args = parser.parse_args()

    from ctrader_open_api import Client, EndPoints, Protobuf, TcpProtocol
    from ctrader_open_api.messages.OpenApiMessages_pb2 import (
        ProtoOAAccountAuthReq,
        ProtoOAAccountAuthRes,
        ProtoOAApplicationAuthReq,
        ProtoOAApplicationAuthRes,
        ProtoOAErrorRes,
        ProtoOASymbolByIdReq,
        ProtoOASymbolByIdRes,
        ProtoOASymbolsListReq,
        ProtoOASymbolsListRes,
    )

    credentials = load_credentials()
    account_id = int(credentials["account_id"])
    wanted = tuple(dict.fromkeys(normalize_symbol(s) for s in args.symbols))
    client = Client(
        EndPoints.PROTOBUF_DEMO_HOST, EndPoints.PROTOBUF_PORT, TcpProtocol
    )
    result: dict = {"account_id": account_id, "host": "demo", "symbols": []}
    light_by_id: dict[int, object] = {}
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
        client.send(request).addErrback(
            lambda failure: finish(str(failure.value))
        )

    def on_message(_client, message) -> None:
        payload_type = message.payloadType
        if payload_type == ProtoOAErrorRes().payloadType:
            error = Protobuf.extract(message)
            finish(f"{error.errorCode}: {getattr(error, 'description', '')}")
            return
        if payload_type == ProtoOAApplicationAuthRes().payloadType:
            request = ProtoOAAccountAuthReq()
            request.ctidTraderAccountId = account_id
            request.accessToken = credentials["access_token"]
            send(request)
            return
        if payload_type == ProtoOAAccountAuthRes().payloadType:
            request = ProtoOASymbolsListReq()
            request.ctidTraderAccountId = account_id
            send(request)
            return
        if payload_type == ProtoOASymbolsListRes().payloadType:
            response = Protobuf.extract(message)
            available = {
                normalize_symbol(symbol.symbolName): symbol
                for symbol in response.symbol
            }
            missing = [symbol for symbol in wanted if symbol not in available]
            result["missing"] = missing
            matches = [available[symbol] for symbol in wanted if symbol in available]
            light_by_id.update({int(symbol.symbolId): symbol for symbol in matches})
            if not matches:
                finish()
                return
            request = ProtoOASymbolByIdReq()
            request.ctidTraderAccountId = account_id
            request.symbolId.extend(symbol.symbolId for symbol in matches)
            send(request)
            return
        if payload_type == ProtoOASymbolByIdRes().payloadType:
            response = Protobuf.extract(message)
            details_by_id = {
                int(symbol.symbolId): symbol for symbol in response.symbol
            }
            for symbol in wanted:
                light = next(
                    (
                        item
                        for item in light_by_id.values()
                        if normalize_symbol(item.symbolName) == symbol
                    ),
                    None,
                )
                if light is None:
                    continue
                details = details_by_id.get(int(light.symbolId))
                if details is None:
                    continue
                result["symbols"].append(
                    {
                        "requested": symbol,
                        "name": light.symbolName,
                        "symbol_id": int(light.symbolId),
                        "digits": int(details.digits),
                        "pip_position": int(details.pipPosition),
                        "pip_size": 10 ** (-int(details.pipPosition)),
                        "min_volume": int(details.minVolume),
                        "step_volume": int(details.stepVolume),
                        "lot_size": int(details.lotSize),
                    }
                )
            finish()

    def on_connected(_client) -> None:
        request = ProtoOAApplicationAuthReq()
        request.clientId = credentials["client_id"]
        request.clientSecret = credentials["client_secret"]
        send(request)

    client.setConnectedCallback(on_connected)
    client.setMessageReceivedCallback(on_message)
    client.startService()
    reactor.callLater(30, lambda: finish("symbol metadata request timed out"))
    reactor.run()

    output = json.dumps(result, indent=2)
    if args.json_out:
        Path(args.json_out).write_text(output + "\n")
    print(output)
    return 1 if result.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
