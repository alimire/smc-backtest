#!/usr/bin/env python3
"""Refresh cTrader Open API access token (demo credentials only)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CRED_PATH = ROOT / "credentials" / "demo.json"
ALLOWED_ACCOUNT_ID = 47_820_966


def main() -> int:
    if not CRED_PATH.exists():
        print(f"Missing {CRED_PATH}")
        return 1
    creds = json.loads(CRED_PATH.read_text())
    if str(creds.get("host", "")).lower() != "demo":
        print("Refusing: host must be demo")
        return 2
    if int(creds.get("account_id") or 0) != ALLOWED_ACCOUNT_ID:
        print(f"Refusing: account_id must be {ALLOWED_ACCOUNT_ID}")
        return 2
    refresh = (creds.get("refresh_token") or "").strip()
    client_id = (creds.get("client_id") or "").strip()
    client_secret = (creds.get("client_secret") or "").strip()
    if not refresh or not client_id or not client_secret:
        print("Missing refresh_token / client_id / client_secret")
        return 1

    from ctrader_open_api import Auth

    auth = Auth(client_id, client_secret, creds.get("redirect_uri", "http://127.0.0.1:8080/redirect"))
    token = auth.refreshToken(refresh)
    if "accessToken" not in token:
        print("Token refresh failed:", {k: token.get(k) for k in ("error", "error_description", "errorCode")})
        return 1

    creds["access_token"] = token["accessToken"]
    if token.get("refreshToken"):
        creds["refresh_token"] = token["refreshToken"]
    creds["host"] = "demo"
    CRED_PATH.write_text(json.dumps(creds, indent=2) + "\n")
    print("Refreshed demo access_token (host=demo).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
