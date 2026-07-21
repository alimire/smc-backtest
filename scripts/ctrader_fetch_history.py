#!/usr/bin/env python3
"""
Fetch EURUSD historical trend bars from cTrader Open API (demo only).

Chunks requests to respect ~14k bar limit. Writes CSV compatible with data_fetch cache.
Does NOT print secrets.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from twisted.internet import reactor

ROOT = Path(__file__).resolve().parent.parent
CRED_PATH = ROOT / "credentials" / "demo.json"
CACHE_DIR = ROOT / "data_cache"

ALLOWED_ACCOUNT_ID = 47_820_966


def load_credentials() -> dict:
    if not CRED_PATH.exists():
        print(f"Missing {CRED_PATH}")
        sys.exit(1)
    creds = json.loads(CRED_PATH.read_text())
    if not creds.get("access_token"):
        print("No access_token — run: python3 scripts/ctrader_oauth.py")
        sys.exit(1)
    if str(creds.get("host", "")).lower() != "demo":
        print("Refusing: credentials host must be demo")
        sys.exit(1)
    account_id = int(creds.get("account_id") or 0)
    if account_id != ALLOWED_ACCOUNT_ID:
        print(f"Refusing: account_id must be {ALLOWED_ACCOUNT_ID}")
        sys.exit(1)
    return creds


def decode_bar(low: int, delta_open: int, delta_high: int, delta_close: int, digits: int) -> tuple[float, float, float, float]:
    base = low / 100_000.0
    o = round(base + delta_open / 100_000.0, digits)
    h = round(base + delta_high / 100_000.0, digits)
    l = round(base, digits)
    c = round(base + delta_close / 100_000.0, digits)
    return o, h, l, c


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Fetch cTrader historical bars")
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--period", default="M15", choices=["M1", "M5", "M15", "M30", "H1", "H4", "D1"])
    parser.add_argument("--days", type=int, default=730, help="Calendar days to request (chunked)")
    parser.add_argument("--out", default="", help="Output CSV path (default: data_cache/...)")
    args = parser.parse_args()

    try:
        from ctrader_open_api import Client, EndPoints, Protobuf, TcpProtocol
        from ctrader_open_api.messages.OpenApiMessages_pb2 import (
            ProtoOAAccountAuthReq,
            ProtoOAAccountAuthRes,
            ProtoOAApplicationAuthReq,
            ProtoOAApplicationAuthRes,
            ProtoOAErrorRes,
            ProtoOAGetTrendbarsReq,
            ProtoOAGetTrendbarsRes,
            ProtoOASymbolsListReq,
            ProtoOASymbolsListRes,
        )
        from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOATrendbarPeriod
    except ImportError:
        print("Install: pip install ctrader-open-api twisted")
        sys.exit(1)

    period_map = {
        "M1": ProtoOATrendbarPeriod.M1,
        "M5": ProtoOATrendbarPeriod.M5,
        "M15": ProtoOATrendbarPeriod.M15,
        "M30": ProtoOATrendbarPeriod.M30,
        "H1": ProtoOATrendbarPeriod.H1,
        "H4": ProtoOATrendbarPeriod.H4,
        "D1": ProtoOATrendbarPeriod.D1,
    }
    period_minutes = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}

    creds = load_credentials()
    account_id = int(creds["account_id"])
    client = Client(EndPoints.PROTOBUF_DEMO_HOST, EndPoints.PROTOBUF_PORT, TcpProtocol)

    state = {
        "account_id": account_id,
        "symbol_id": None,
        "symbol_digits": 5,
        "all_bars": [],
        "chunk_end": datetime.now(timezone.utc),
        "chunk_start": datetime.now(timezone.utc) - timedelta(days=args.days),
        "period": period_map[args.period],
        "period_label": args.period,
        "days": args.days,
        "pending_chunks": [],
        "done": False,
    }

    def schedule_chunks():
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=args.days)
        mins = period_minutes[args.period]
        # ~90 days per chunk for M15 (~8640 bars) stays under 14k limit
        chunk_days = max(30, min(120, int(14_000 * mins / (24 * 60))))
        chunks = []
        cur_end = end
        while cur_end > start:
            cur_start = max(start, cur_end - timedelta(days=chunk_days))
            chunks.append((cur_start, cur_end))
            cur_end = cur_start
        state["pending_chunks"] = list(reversed(chunks))
        print(f"Scheduled {len(chunks)} chunks for {args.period} over {args.days}d")

    def on_error(failure):
        print("ERROR:", failure)
        if reactor.running:
            reactor.stop()

    def request_next_chunk():
        if not state["pending_chunks"]:
            finish()
            return
        c_start, c_end = state["pending_chunks"].pop(0)
        req = ProtoOAGetTrendbarsReq()
        req.ctidTraderAccountId = state["account_id"]
        req.symbolId = state["symbol_id"]
        req.period = state["period"]
        req.fromTimestamp = int(c_start.timestamp() * 1000)
        req.toTimestamp = int(c_end.timestamp() * 1000)
        print(f"Requesting chunk {c_start.date()} -> {c_end.date()} ...")
        client.send(req).addErrback(on_error)

    def finish():
        bars = state["all_bars"]
        if not bars:
            print("No bars received")
            reactor.stop()
            return
        df = pd.DataFrame(bars)
        df = df.drop_duplicates(subset=["ts"]).sort_values("ts")
        df.index = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.tz_convert("Europe/London")
        df.index.name = "Datetime"
        out_df = df[["Open", "High", "Low", "Close", "Volume"]]
        safe_sym = args.symbol.replace("/", "_")
        out_path = Path(args.out) if args.out else CACHE_DIR / f"{safe_sym}_ctrader_{args.period}_{args.days}d.csv"
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        out_df.to_csv(out_path)
        print(f"Saved {len(out_df)} bars -> {out_path}")
        print(f"Range: {out_df.index[0]} -> {out_df.index[-1]}")
        reactor.stop()

    def on_message(_client, message):
        ptype = message.payloadType

        if ptype == ProtoOAErrorRes().payloadType:
            err = Protobuf.extract(message)
            print(f"ERROR: {err.errorCode}: {getattr(err, 'description', '')}")
            if reactor.running:
                reactor.stop()
            return

        if ptype == ProtoOAApplicationAuthRes().payloadType:
            print("OK Application authorized")
            req = ProtoOAAccountAuthReq()
            req.ctidTraderAccountId = state["account_id"]
            req.accessToken = creds["access_token"]
            client.send(req).addErrback(on_error)
            return

        if ptype == ProtoOAAccountAuthRes().payloadType:
            print(f"OK Account {state['account_id']} authorized")
            req = ProtoOASymbolsListReq()
            req.ctidTraderAccountId = state["account_id"]
            client.send(req).addErrback(on_error)
            return

        if ptype == ProtoOASymbolsListRes().payloadType:
            res = Protobuf.extract(message)
            target = args.symbol.upper()
            matches = []
            for s in res.symbol:
                name = getattr(s, "symbolName", "")
                if target in name.upper():
                    matches.append(s)
            if not matches:
                print(f"No symbol matching {target}")
                reactor.stop()
                return
            sym = matches[0]
            state["symbol_id"] = sym.symbolId
            state["symbol_digits"] = getattr(sym, "digits", 5)
            print(f"OK Symbol {getattr(sym, 'symbolName', target)} id={sym.symbolId} digits={state['symbol_digits']}")
            schedule_chunks()
            request_next_chunk()
            return

        if ptype == ProtoOAGetTrendbarsRes().payloadType:
            res = Protobuf.extract(message)
            digits = state["symbol_digits"]
            n = 0
            for tb in res.trendbar:
                ts = tb.utcTimestampInMinutes * 60 * 1000
                o, h, l, c = decode_bar(tb.low, tb.deltaOpen, tb.deltaHigh, tb.deltaClose, digits)
                vol = getattr(tb, "volume", 0) or 0
                state["all_bars"].append({
                    "ts": ts,
                    "Open": o,
                    "High": h,
                    "Low": l,
                    "Close": c,
                    "Volume": float(vol),
                })
                n += 1
            print(f"  received {n} bars (total {len(state['all_bars'])})")
            request_next_chunk()
            return

    def connected(_client):
        print("Connected (demo)")
        req = ProtoOAApplicationAuthReq()
        req.clientId = creds["client_id"]
        req.clientSecret = creds["client_secret"]
        client.send(req).addErrback(on_error)

    def on_timeout():
        print("ERROR: fetch timed out (no response)")
        if reactor.running:
            reactor.stop()

    client.setConnectedCallback(connected)
    client.setMessageReceivedCallback(on_message)
    client.startService()
    reactor.callLater(240, on_timeout)
    reactor.run()


if __name__ == "__main__":
    main()
