"""
SMC Backtest Web App — Flask server
Salim can run backtests from any browser.
"""

from flask import Flask, render_template, request, jsonify
import threading
import traceback

from smc_detector import scan_setups, TradeSetup
from simulator_core import simulate_rows
from dataclasses import asdict

app = Flask(__name__)

# Store last result in memory (fine for single-user use)
_last_result: list[TradeSetup] = []
_running = False


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/run", methods=["POST"])
def run_backtest():
    global _last_result, _running
    if _running:
        return jsonify({"error": "Backtest already running, please wait."}), 429

    data = request.json or {}
    symbol   = data.get("symbol",   "EURUSD=X")
    days     = int(data.get("days", 60))
    session  = data.get("session",  "both")
    interval = data.get("interval", "15m")
    min_rr   = float(data.get("min_rr", 2.0))

    _running = True
    try:
        setups = scan_setups(
            symbol=symbol,
            days=days,
            session=session,
            interval=interval,
            min_rr=min_rr,
        )
        _last_result = setups

        rows = []
        for s in setups:
            d = asdict(s)
            d["time"] = str(s.time)
            rows.append(d)

        aplus = [r for r in rows if r["is_aplus"]]

        try:
            sim = simulate_rows(rows, symbol=symbol, days=days, interval=interval)
            rows = sim["rows"]
        except Exception as sim_err:
            sim = {
                "sim_wins": 0, "sim_losses": 0, "sim_unresolved": 0,
                "sim_win_rate": 0.0, "avg_planned_rr": 0.0, "expectancy_r": 0.0,
                "sim_error": str(sim_err),
            }

        return jsonify({
            "total":     len(rows),
            "aplus":     len(aplus),
            "smt_count": sum(1 for r in aplus if r["smt_confluence"]),
            "sim_wins":       sim.get("sim_wins", 0),
            "sim_losses":     sim.get("sim_losses", 0),
            "sim_unresolved": sim.get("sim_unresolved", 0),
            "sim_win_rate":   sim.get("sim_win_rate", 0),
            "avg_planned_rr": sim.get("avg_planned_rr", 0),
            "expectancy_r":   sim.get("expectancy_r", 0),
            "sim_error":      sim.get("sim_error"),
            "rows":      rows,
        })
    except Exception as e:
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500
    finally:
        _running = False


if __name__ == "__main__":
    # 0.0.0.0 so Railway/Render can expose it; debug=False for production
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
