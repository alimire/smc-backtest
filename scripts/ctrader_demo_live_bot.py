#!/usr/bin/env python3
"""Headless DEMO bot: research_best SMC scan → Fusion cTrader demo orders.

Safety:
  - Requires DEMO=1 in the environment (hard-coded gate)
  - credentials/demo.json host must be "demo"
  - Fusion demo login 10123191 / account 47820966 only
  - Places DEMO orders only (never live hosts)

Scans EURUSD and XAUUSD (Fusion gold). One open position per symbol.

Runs forever (or --once). Designed for Windows Task Scheduler / NSSM.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from advanced_gates import get_scan_preset  # noqa: E402
from smc_detector import scan_setups  # noqa: E402

STATE_PATH = ROOT / ".live_bot_state.json"
LOG_DIR = ROOT / "reports"
SERVICE_LOG = LOG_DIR / "demo_live_bot_service.log"
EVENT_LOG = LOG_DIR / "demo_live_bot.jsonl"
CRED_PATH = ROOT / "credentials" / "demo.json"

ALLOWED_ACCOUNT_ID = 47_820_966
ALLOWED_LOGIN = 10_123_191

# (broker_symbol, scan_symbol for detector/yahoo fallback)
# Active DEMO symbols. New FX majors added after Jul 2026 multi-symbol OOS
# study (research_best / Legacy chop_loose). All new pairs trade broker min volume.
TRADE_SYMBOLS = (
    ("EURUSD", "EURUSD=X"),
    ("XAUUSD", "XAUUSD=X"),
    ("GBPUSD", "GBPUSD=X"),
    ("USDJPY", "USDJPY=X"),
    ("AUDUSD", "AUDUSD=X"),
    ("USDCAD", "USDCAD=X"),
    ("NZDUSD", "NZDUSD=X"),
    ("USDCHF", "USDCHF=X"),
    # US Dollar Index (Fusion demo id 120, pip 0.01, minVolume 100).
    # OOS Jul 2026: n=38, WR 26.3%, E[R]=6.30, PF=9.55 — PASS.
    ("USDX", "USDX=X"),
)


def require_demo_env() -> None:
    if os.environ.get("DEMO", "").strip() != "1":
        raise SystemExit("Refusing to start: set DEMO=1 (demo-only hard gate)")
    if not CRED_PATH.exists():
        raise SystemExit(f"Missing {CRED_PATH}")
    creds = json.loads(CRED_PATH.read_text())
    if str(creds.get("host", "")).lower() != "demo":
        raise SystemExit("Refusing: credentials host must be demo")
    if int(creds.get("account_id") or 0) != ALLOWED_ACCOUNT_ID:
        raise SystemExit(f"Refusing: account_id must be {ALLOWED_ACCOUNT_ID}")


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(SERVICE_LOG, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def append_event(payload: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with EVENT_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, default=str) + "\n")


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"seen_setups": []}
    try:
        return json.loads(STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {"seen_setups": []}


def save_state(state: dict) -> None:
    seen = list(dict.fromkeys(state.get("seen_setups", [])))[-2000:]
    STATE_PATH.write_text(
        json.dumps({"seen_setups": seen, "updated": datetime.now(timezone.utc).isoformat()}, indent=2)
        + "\n"
    )


def setup_key(broker_symbol: str, setup) -> str:
    t = setup.time
    if getattr(t, "tzinfo", None) is not None:
        t = t.astimezone(timezone.utc).replace(tzinfo=None)
    # Price precision from broker digits: gold 2, JPY/USDX 3, major FX 5
    u = broker_symbol.upper()
    if "XAU" in u:
        prec = 2
    elif u.endswith("JPY") or u == "USDX":
        prec = 3
    else:
        prec = 5
    return (
        f"{broker_symbol}|{t.isoformat()}|{setup.direction}|"
        f"{setup.entry_price:.{prec}f}|{setup.stop_loss:.{prec}f}"
    )


def run_py(script: str, *args: str, timeout: int = 180) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(ROOT / "scripts" / script), *args]
    env = os.environ.copy()
    env["DEMO"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def refresh_token() -> None:
    proc = run_py("ctrader_token_refresh.py", timeout=60)
    if proc.returncode == 0:
        logging.info("Token refresh OK")
    else:
        logging.warning(
            "Token refresh failed (rc=%s): %s %s",
            proc.returncode,
            (proc.stdout or "")[-400:],
            (proc.stderr or "")[-400:],
        )


def fetch_bars(broker_symbol: str, days: int) -> Path:
    """Fetch M15 bars into the broker cache path expected by fetch_broker_15m."""
    out = ROOT / "data_cache" / f"{broker_symbol}_ctrader_M15_{days}d.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = run_py(
        "ctrader_fetch_history.py",
        "--symbol",
        broker_symbol,
        "--period",
        "M15",
        "--days",
        str(days),
        "--out",
        str(out),
        timeout=300,
    )
    if proc.stdout:
        for line in (proc.stdout or "").strip().splitlines()[-15:]:
            logging.info("[fetch:%s] %s", broker_symbol, line)
    if proc.returncode != 0 or not out.exists() or out.stat().st_size < 100:
        raise RuntimeError(
            f"bar fetch failed for {broker_symbol}: rc={proc.returncode} out={out} "
            f"stdout={(proc.stdout or '')[-500:]} stderr={(proc.stderr or '')[-500:]}"
        )
    logging.info(
        "Fetched %s cTrader M15 bars (%sd) -> %s (%s bytes)",
        broker_symbol,
        days,
        out,
        out.stat().st_size,
    )
    return out


def scan_research_best(broker_symbol: str, scan_symbol: str, days: int, max_age_minutes: int) -> list:
    recipe = get_scan_preset("research_best")
    logging.info(
        "Scanning %s research_best=%s mode=%s session=%s filters=%s",
        broker_symbol,
        recipe.name,
        recipe.mode,
        recipe.session,
        recipe.filters,
    )
    # Gold: slightly wider SL buffer in pip units (pip=0.1 → 5 pips = $0.50)
    sl_buffer_pips = 5.0
    setups = scan_setups(
        symbol=scan_symbol,
        days=days,
        interval=recipe.interval,
        session=recipe.session,
        min_rr=float(recipe.min_rr),
        strategy_mode=recipe.mode,
        advanced_cfg=recipe.advanced,
        scan_filters=dict(recipe.filters),
        data_source="ctrader",
        quiet=True,
        sl_buffer_pips=sl_buffer_pips,
    )
    now = datetime.now()
    cutoff = now - timedelta(minutes=max_age_minutes)
    fresh = []
    for s in setups:
        if not s.is_aplus:
            continue
        st = s.time.replace(tzinfo=None) if getattr(s.time, "tzinfo", None) else s.time
        if st < cutoff:
            continue
        fresh.append(s)
    logging.info(
        "%s scan complete: %s total setups, %s fresh A+ in last %sm",
        broker_symbol,
        len(setups),
        len(fresh),
        max_age_minutes,
    )
    return fresh


def place_setup(broker_symbol: str, setup, risk: float) -> int:
    key = setup_key(broker_symbol, setup)
    side = "long" if setup.direction == "long" else "short"
    proc = run_py(
        "ctrader_demo_place_setup.py",
        "--setup-id",
        key,
        "--symbol",
        broker_symbol,
        "--side",
        side,
        "--entry",
        str(setup.entry_price),
        "--sl",
        str(setup.stop_loss),
        "--tp",
        str(setup.take_profit),
        "--risk",
        str(risk),
        "--i-confirm-demo-order",
        timeout=120,
    )
    if proc.stdout:
        for line in proc.stdout.strip().splitlines()[-20:]:
            logging.info("[place] %s", line)
    if proc.stderr:
        for line in proc.stderr.strip().splitlines()[-10:]:
            logging.warning("[place:err] %s", line)
    append_event(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "place_result",
            "symbol": broker_symbol,
            "setup_id": key,
            "side": side,
            "entry": setup.entry_price,
            "sl": setup.stop_loss,
            "tp": setup.take_profit,
            "rr": getattr(setup, "rr_ratio", None),
            "returncode": proc.returncode,
        }
    )
    return proc.returncode


def cycle(args: argparse.Namespace) -> None:
    refresh_token()
    state = load_state()
    seen = set(state.get("seen_setups", []))

    for broker_symbol, scan_symbol in TRADE_SYMBOLS:
        try:
            fetch_bars(broker_symbol, args.fetch_days)
            fresh = scan_research_best(
                broker_symbol, scan_symbol, args.fetch_days, args.max_age
            )
        except Exception as exc:
            logging.error("%s cycle fetch/scan failed: %s", broker_symbol, exc)
            append_event(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "event": "error",
                    "symbol": broker_symbol,
                    "error": str(exc),
                }
            )
            continue

        for setup in fresh:
            key = setup_key(broker_symbol, setup)
            if key in seen:
                logging.info("Skip already-seen setup %s", key)
                continue
            logging.info(
                "NEW A+ %s %s entry=%s sl=%s tp=%s rr=%s",
                broker_symbol,
                setup.direction,
                setup.entry_price,
                setup.stop_loss,
                setup.take_profit,
                getattr(setup, "rr_ratio", "?"),
            )
            append_event(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "event": "signal",
                    "symbol": broker_symbol,
                    "setup_id": key,
                    "direction": setup.direction,
                    "entry": setup.entry_price,
                    "sl": setup.stop_loss,
                    "tp": setup.take_profit,
                    "session": getattr(setup, "session", None),
                    "notes": getattr(setup, "notes", None),
                }
            )
            if args.dry_run:
                logging.info("DRY RUN — would place DEMO order for %s", key)
                seen.add(key)
                continue
            rc = place_setup(broker_symbol, setup, args.risk)
            # Mark seen even on block (duplicate / flat account) to avoid spam;
            # only retry if hard transport failure (rc==1).
            if rc != 1:
                seen.add(key)
            if rc == 0:
                logging.info("DEMO ENTRY filled for %s", key)
            elif rc == 2:
                logging.info("Setup blocked by policy for %s", key)
            else:
                logging.error("Order error for %s (rc=%s)", key, rc)

    state["seen_setups"] = sorted(seen)
    save_state(state)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Headless research_best DEMO bot")
    p.add_argument("--once", action="store_true", help="Run one cycle and exit")
    p.add_argument("--interval", type=int, default=60, help="Seconds between cycles")
    p.add_argument("--fetch-days", type=int, default=30, help="M15 history window")
    p.add_argument("--max-age", type=int, default=45, help="Only trade setups younger than N minutes")
    p.add_argument("--risk", type=float, default=0.25, help="Risk %% capped by policy at 0.25")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan/log only — never place orders",
    )
    return p.parse_args()


def main() -> int:
    require_demo_env()
    setup_logging()
    args = parse_args()
    symbols = ",".join(s for s, _ in TRADE_SYMBOLS)
    logging.info(
        "Starting SMC DEMO bot | account=%s login=%s | symbols=%s | research_best | dry_run=%s",
        ALLOWED_ACCOUNT_ID,
        ALLOWED_LOGIN,
        symbols,
        args.dry_run,
    )
    append_event(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "service_start",
            "symbols": symbols,
            "dry_run": args.dry_run,
            "interval": args.interval,
            "fetch_days": args.fetch_days,
            "max_age": args.max_age,
        }
    )

    while True:
        try:
            cycle(args)
        except Exception as exc:
            logging.error("Cycle failed: %s\n%s", exc, traceback.format_exc())
            append_event(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "event": "error",
                    "error": str(exc),
                }
            )
        if args.once:
            break
        logging.info("Sleeping %ss...", args.interval)
        time.sleep(max(15, args.interval))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
