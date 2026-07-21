"""
SMC Backtest Web App — Flask server
Salim can run backtests from any browser.
"""

from flask import Flask, render_template, request, jsonify, make_response
import traceback

from advanced_gates import get_preset, get_scan_preset
from smc_detector import scan_setups, TradeSetup
from simulator_core import simulate_rows
from dataclasses import asdict

app = Flask(__name__)

# Store last result in memory (fine for single-user use)
_last_result: list[TradeSetup] = []
_running = False


@app.route("/")
def index():
    resp = make_response(render_template("index.html"))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp


@app.route("/presets")
def list_presets():
    """Expose scan recipes for the UI (research_best = OOS Legacy chop_loose)."""
    from advanced_gates import SCAN_PRESETS, PRESETS

    scan = {}
    for key, recipe in SCAN_PRESETS.items():
        scan[key] = {
            "name": recipe.name,
            "mode": recipe.mode,
            "session": recipe.session,
            "min_rr": recipe.min_rr,
            "filters": dict(recipe.filters),
            "data_source": recipe.data_source,
            "interval": recipe.interval,
            "label": recipe.label,
        }
    return jsonify({
        "scan_presets": scan,
        "advanced_presets": sorted(PRESETS.keys()),
    })


@app.route("/run", methods=["POST"])
def run_backtest():
    global _last_result, _running
    if _running:
        return jsonify({"error": "Backtest already running, please wait."}), 429

    data = request.json or {}
    symbol = data.get("symbol", "EURUSD=X")
    days = int(data.get("days", 60))
    session = data.get("session", "both")
    interval = data.get("interval", "15m")
    min_rr = float(data.get("min_rr", 2.0))
    strategy_mode = data.get("strategy_mode", "advanced")
    preset = (data.get("preset") or "").strip().lower()
    data_source = (data.get("data_source") or "yahoo").strip().lower()
    scan_filters = data.get("scan_filters") or None
    advanced_cfg = None
    recipe_meta = None

    if preset:
        # Prefer full scan recipes (research_best = Legacy + chop_loose).
        try:
            recipe = get_scan_preset(preset)
            strategy_mode = recipe.mode
            session = recipe.session
            min_rr = float(recipe.min_rr)
            interval = recipe.interval or interval
            data_source = recipe.data_source or data_source
            scan_filters = dict(recipe.filters)
            advanced_cfg = recipe.advanced
            recipe_meta = {
                "name": recipe.name,
                "variant": recipe.name,
                "label": recipe.label,
                "mode": recipe.mode,
                "session": recipe.session,
                "min_rr": recipe.min_rr,
                "filters": dict(recipe.filters),
                "data_source": recipe.data_source,
            }
            # Full cTrader cache when research_best / broker source selected.
            if data_source == "ctrader" and days < 365:
                days = 730
        except KeyError:
            if strategy_mode == "advanced":
                try:
                    advanced_cfg = get_preset(preset)
                except KeyError:
                    return jsonify({"error": f"Unknown preset: {preset}"}), 400
            else:
                return jsonify({"error": f"Unknown preset: {preset}"}), 400

    _running = True
    try:
        setups, debug = scan_setups(
            symbol=symbol,
            days=days,
            session=session,
            interval=interval,
            min_rr=min_rr,
            strategy_mode=strategy_mode,
            advanced_cfg=advanced_cfg,
            scan_filters=scan_filters,
            data_source=data_source,
            return_debug=True,
        )
        _last_result = setups

        rows = []
        for s in setups:
            d = asdict(s)
            d["time"] = str(s.time)
            rows.append(d)

        aplus = [r for r in rows if r["is_aplus"]]
        a_tier = [r for r in rows if r.get("tier") == "A"]

        try:
            # aplus_only=False: A-tier rows are accepted setups too and must be
            # simulated; scanner emits only tradeable rows.
            sim = simulate_rows(
                rows,
                symbol=symbol,
                days=days,
                interval=interval,
                candles=debug.get("candles"),
                aplus_only=False,
            )
            rows = sim["rows"]
        except Exception as sim_err:
            sim = {
                "sim_wins": 0, "sim_losses": 0, "sim_unresolved": 0,
                "sim_win_rate": 0.0, "avg_planned_rr": 0.0, "expectancy_r": 0.0,
                "profit_factor": 0.0,
                "sim_error": str(sim_err),
            }

        from simulator_core import compute_metrics

        def tier_stats(tier_rows):
            m = compute_metrics(tier_rows)
            return {
                "n": m["setups"],
                "resolved": m["resolved"],
                "win_rate": m["sim_win_rate"],
                "expectancy_r": m["expectancy_r"],
                "profit_factor": m["profit_factor"],
                "wins": m["sim_wins"],
                "losses": m["sim_losses"],
            }

        return jsonify({
            "total":     len(rows),
            "aplus":     len(aplus),
            "a_tier":    len(a_tier),
            "tiers": {
                "A+": tier_stats([r for r in rows if r.get("tier", "A+") == "A+"]),
                "A": tier_stats([r for r in rows if r.get("tier") == "A"]),
            },
            "smt_count": sum(1 for r in aplus if r["smt_confluence"]),
            "sim_wins":       sim.get("sim_wins", 0),
            "sim_losses":     sim.get("sim_losses", 0),
            "sim_unresolved": sim.get("sim_unresolved", 0),
            "sim_win_rate":   sim.get("sim_win_rate", 0),
            "avg_planned_rr": sim.get("avg_planned_rr", 0),
            "expectancy_r":   sim.get("expectancy_r", 0),
            "profit_factor":   sim.get("profit_factor", 0),
            "sim_error":      sim.get("sim_error"),
            "strategy_mode":  debug.get("strategy_mode", strategy_mode),
            "preset": preset or None,
            "recipe": recipe_meta,
            "data_source": debug.get("data_source", data_source),
            "data_bars": len(debug.get("candles") or []),
            "data_days": days,
            "scan_filters": debug.get("scan_filters"),
            "session": session,
            "min_rr": min_rr,
            "days": days,
            "interval": interval,
            "strategy_version": debug.get("strategy_version", ""),
            "reject_counts":  debug.get("reject_counts", {}),
            "fbos_count":     debug.get("fbos_count", 0),
            "rbos_count":     debug.get("rbos_count", 0),
            "cisd_count":     debug.get("cisd_count", 0),
            "amd_count":      debug.get("amd_count", 0),
            "rows":      rows,
        })
    except Exception as e:
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500
    finally:
        _running = False


if __name__ == "__main__":
    # 0.0.0.0 so Railway/Render can expose it; debug=False for production
    import os
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port, debug=False)
