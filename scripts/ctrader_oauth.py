#!/usr/bin/env python3
"""
One-time OAuth setup for cTrader Open API (Fusion demo).

1. Register an app at https://openapi.ctrader.com/apps (status must be Active)
2. Copy credentials/demo.example.json → credentials/demo.json
3. Fill client_id, client_secret, redirect_uri (must match app registration)
4. Run: python3 scripts/ctrader_oauth.py

Opens browser, you approve access, then saves tokens to credentials/demo.json.
"""

import json
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CRED_PATH = ROOT / "credentials" / "demo.json"


def main() -> None:
    try:
        from ctrader_open_api import Auth
    except ImportError:
        print("Install deps: pip install ctrader-open-api")
        sys.exit(1)

    if not CRED_PATH.exists():
        print(f"Create {CRED_PATH} from credentials/demo.example.json first.")
        sys.exit(1)

    creds = json.loads(CRED_PATH.read_text())
    client_id = creds.get("client_id", "").strip()
    client_secret = creds.get("client_secret", "").strip()
    redirect_uri = creds.get("redirect_uri", "http://localhost:8080").strip()

    if not client_id or client_id.startswith("YOUR_"):
        print("Set client_id and client_secret in credentials/demo.json")
        sys.exit(1)

    auth = Auth(client_id, client_secret, redirect_uri)
    auth_uri = auth.getAuthUri()
    print("\n1. Open this URL in your browser and approve access:\n")
    print(auth_uri)
    print("\n2. After redirect, copy the FULL redirect URL from the browser address bar.")
    print("   It contains ?code=...\n")
    webbrowser.open(auth_uri)

    redirect_url = input("Paste the full redirect URL here: ").strip()
    if "code=" not in redirect_url:
        print("No code= found in URL.")
        sys.exit(1)
    code = redirect_url.split("code=")[1].split("&")[0]

    token = auth.getToken(code)
    if "accessToken" not in token:
        print("Token exchange failed:", token)
        sys.exit(1)

    creds["access_token"] = token["accessToken"]
    creds["refresh_token"] = token.get("refreshToken", creds.get("refresh_token", ""))
    creds["host"] = "demo"

    CRED_PATH.write_text(json.dumps(creds, indent=2))
    print(f"\nSaved tokens to {CRED_PATH}")
    print("Next: python3 scripts/ctrader_connect_test.py")


if __name__ == "__main__":
    main()
