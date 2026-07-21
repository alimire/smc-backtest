#!/usr/bin/env python3
"""
Live SMC scanner — same logic as backtest, alerts on recent A+ setups.

Usage:
  python3 scripts/live_scan_alert.py                    # EURUSD, last 5 days
  python3 scripts/live_scan_alert.py --symbol EURUSD=X --notify
  python3 scripts/live_scan_alert.py --watch            # poll every 15 min

Uses .alert_state.json to avoid duplicate notifications.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from smc_detector import scan_setups  # noqa: E402

STATE_PATH = ROOT / ".alert_state.json"


def load_state() -> set[str]:
    if not STATE_PATH.exists():
        return set()
    try:
        data = json.loads(STATE_PATH.read_text())
        return set(data.get("seen", []))
    except (json.JSONDecodeError, OSError):
        return set()


def save_state(seen: set[str]) -> None:
    STATE_PATH.write_text(json.dumps({"seen": sorted(seen)[-500:]}, indent=2))


def setup_key(s) -> str:
    return f"{s.time.isoformat()}|{s.direction}|{s.entry_price}"


def mac_notify(title: str, message: str) -> None:
    safe = message.replace('"', "'")[:200]
    script = f'display notification "{safe}" with title "{title}" sound name "Ping"'
    try:
        subprocess.run(["osascript", "-e", script], check=False, capture_output=True)
    except OSError:
        pass


def scan_and_alert(
    symbol: str,
    days: int,
    session: str,
    max_age_minutes: int,
    notify: bool,
    aplus_only: bool,
) -> list:
    setups = scan_setups(symbol=symbol, days=days, session=session, interval="15m")
    seen = load_state()
    now = datetime.now()
    cutoff = now - timedelta(minutes=max_age_minutes)
    new_alerts = []

    for s in setups:
        if aplus_only and not s.is_aplus:
            continue
        st = s.time.replace(tzinfo=None) if s.time.tzinfo else s.time
        if st < cutoff:
            continue
        key = setup_key(s)
        if key in seen:
            continue
        seen.add(key)
        new_alerts.append(s)

        line = (
            f"{s.direction.upper()} A+ | {s.session} | {st:%Y-%m-%d %H:%M}\n"
            f"Entry {s.entry_price}  SL {s.stop_loss}  TP {s.take_profit}  RR {s.rr_ratio}\n"
            f"POI {s.poi_type}  IDM {'✓' if s.idm_swept else '✗'}  SMT {'✓' if s.smt_confluence else '✗'}\n"
            f"{s.notes}"
        )
        print("\n" + "=" * 50)
        print(line)
        if notify:
            mac_notify(f"SMC {s.direction.upper()}", line.replace("\n", " | "))

    if new_alerts:
        save_state(seen)
    elif not setups:
        print("No setups in scan window.")
    else:
        print(f"Scanned {len(setups)} setups — no new A+ in last {max_age_minutes} min.")

    return new_alerts


def main() -> None:
    p = argparse.ArgumentParser(description="Live SMC A+ alerts (Salim rules)")
    p.add_argument("--symbol", default="EURUSD=X")
    p.add_argument("--days", type=int, default=5)
    p.add_argument("--session", default="both")
    p.add_argument("--max-age", type=int, default=30, help="Alert setups from last N minutes")
    p.add_argument("--notify", action="store_true", help="macOS notification")
    p.add_argument("--watch", action="store_true", help="Poll every 15 minutes")
    p.add_argument("--all", action="store_true", help="Include non-A+ setups")
    args = p.parse_args()

    if args.watch:
        print("Watching every 15 min… Ctrl+C to stop.")
        while True:
            scan_and_alert(
                args.symbol, args.days, args.session,
                args.max_age, args.notify, not args.all,
            )
            time.sleep(900)
    else:
        scan_and_alert(
            args.symbol, args.days, args.session,
            args.max_age, args.notify, not args.all,
        )


if __name__ == "__main__":
    main()
