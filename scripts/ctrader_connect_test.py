#!/usr/bin/env python3
"""Read-only cTrader Open API test: list Fusion demo accounts and EURUSD symbol."""

import json
import sys
from pathlib import Path

from twisted.internet import reactor

ROOT = Path(__file__).resolve().parent.parent
CRED_PATH = ROOT / "credentials" / "demo.json"


def load_credentials() -> dict:
    if not CRED_PATH.exists():
        print(f"Missing {CRED_PATH}")
        print("Copy credentials/demo.example.json → credentials/demo.json")
        print("Then run: python3 scripts/ctrader_oauth.py")
        sys.exit(1)
    return json.loads(CRED_PATH.read_text())


def main() -> None:
    try:
        from ctrader_open_api import Client, EndPoints, Protobuf, TcpProtocol
        from ctrader_open_api.messages.OpenApiMessages_pb2 import (
            ProtoOAAccountAuthReq,
            ProtoOAApplicationAuthReq,
            ProtoOAApplicationAuthRes,
            ProtoOAAccountAuthRes,
            ProtoOAGetAccountListByAccessTokenReq,
            ProtoOAGetAccountListByAccessTokenRes,
            ProtoOASymbolsListReq,
            ProtoOASymbolsListRes,
            ProtoOATraderReq,
            ProtoOATraderRes,
        )
    except ImportError:
        print("Install deps: pip install ctrader-open-api twisted")
        sys.exit(1)

    creds = load_credentials()
    if not creds.get("access_token"):
        print("No access_token — run: python3 scripts/ctrader_oauth.py")
        sys.exit(1)
    if str(creds.get("host", "")).lower() != "demo":
        print("Refusing connection test: credentials host must be demo")
        sys.exit(1)

    allowed_account_id = 47_820_966
    allowed_login = 10_123_191
    account_id = int(creds.get("account_id") or 0)
    if account_id != allowed_account_id:
        print(
            f"Refusing connection test: account_id must be {allowed_account_id} "
            f"(Fusion demo {allowed_login})"
        )
        sys.exit(1)

    host = EndPoints.PROTOBUF_DEMO_HOST
    client = Client(host, EndPoints.PROTOBUF_PORT, TcpProtocol)
    state = {"account_id": account_id}

    def on_error(failure):
        print("ERROR:", failure)
        reactor.stop()

    def on_message(_client, message):
        ptype = message.payloadType

        if ptype == ProtoOAApplicationAuthRes().payloadType:
            print("OK Application authorized")
            req = ProtoOAGetAccountListByAccessTokenReq()
            req.accessToken = creds["access_token"]
            client.send(req).addErrback(on_error)
            return

        if ptype == ProtoOAGetAccountListByAccessTokenRes().payloadType:
            res = Protobuf.extract(message)
            accounts = list(getattr(res, "ctidTraderAccount", []))
            if not accounts:
                print("No accounts on this token. Authorize OAuth with your Fusion cTID.")
                reactor.stop()
                return
            print("\nLinked accounts:")
            for acc in accounts:
                print(
                    f"  id={acc.ctidTraderAccountId}  "
                    f"login={getattr(acc, 'traderLogin', '?')}  "
                    f"demo={'yes' if not acc.isLive else 'NO — LIVE'}"
                )
            match = next(
                (
                    acc
                    for acc in accounts
                    if int(acc.ctidTraderAccountId) == state["account_id"]
                    and not acc.isLive
                ),
                None,
            )
            if match is None:
                print(
                    f"Allowlisted demo account {state['account_id']} "
                    "is not linked to this token."
                )
                reactor.stop()
                return
            req = ProtoOAAccountAuthReq()
            req.ctidTraderAccountId = state["account_id"]
            req.accessToken = creds["access_token"]
            client.send(req).addErrback(on_error)
            return

        if ptype == ProtoOAAccountAuthRes().payloadType:
            print(f"OK Account {state['account_id']} authorized")
            req = ProtoOATraderReq()
            req.ctidTraderAccountId = state["account_id"]
            client.send(req).addErrback(on_error)
            return

        if ptype == ProtoOATraderRes().payloadType:
            trader = Protobuf.extract(message).trader
            scale = 10 ** getattr(trader, "moneyDigits", 2)
            print(f"\nBalance: ${trader.balance / scale:.2f}")
            req = ProtoOASymbolsListReq()
            req.ctidTraderAccountId = state["account_id"]
            client.send(req).addErrback(on_error)
            return

        if ptype == ProtoOASymbolsListRes().payloadType:
            res = Protobuf.extract(message)
            for s in res.symbol:
                name = getattr(s, "symbolName", "")
                u = name.upper().replace("/", "")
                if u in {"EURUSD", "XAUUSD"} or "XAU" in u:
                    print(f"  {u} → {name}  symbolId={s.symbolId}")
            print("\nOK Open API read-only test passed")
            reactor.callLater(0.3, reactor.stop)

    def connected(_client):
        print(f"Connected ({creds.get('host', 'demo')})")
        req = ProtoOAApplicationAuthReq()
        req.clientId = creds["client_id"]
        req.clientSecret = creds["client_secret"]
        client.send(req).addErrback(on_error)

    client.setConnectedCallback(connected)
    client.setMessageReceivedCallback(on_message)
    client.startService()
    reactor.run()


if __name__ == "__main__":
    main()
