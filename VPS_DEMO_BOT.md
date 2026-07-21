# SMC research_best — DEMO bot (cTrader Open API)

**DEMO ONLY.** Fusion Markets demo login `10123191` / cTrader account `47820966`.
Live trading is hard-blocked (`DEMO=1` required + `host=demo` + allowlisted account).

ATAS ChartStrategy path was **not** chosen — ATAS GUI activation/drawings have been unreliable.
ATAS Platform is still installed under `C:\Program Files (x86)\ATAS Platform` for optional later use.

## Path chosen

**A) Headless cTrader Open API demo bot** on the Windows VPS:
- Preset: `research_best_a` = frozen `research_best` A+ rules **plus** an A tier
  (ONE relaxation: premium/discount zone 0.35/0.65 -> 0.45/0.55; OOS PASS Jul 2026,
  see `reports/tier_study.md`). `SMC_SCAN_PRESET=research_best` reverts to A+ only.
- Orders are labelled `smc-Aplus` (full A+ rules) vs `smc-A` (A tier). A tier
  always trades broker **minimum volume**.
- **Correlation guard:** max **3 total open positions** account-wide (all 9
  symbols are USD-correlated); still one position per symbol.
- Symbols: **EURUSD, XAUUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, NZDUSD, USDCHF, USDX**
  (all passed multi-symbol OOS bar Jul 2026; new FX majors + USDX trade broker min volume;
   gold pip=0.1 / JPY pip=0.01 / USDX pip=0.01, minVolume 100 / majors pip=0.0001)
- Polls ~every 60s, refreshes M15 bars per symbol, scans A+ **and A tier**, places DEMO market orders with SL/TP
- **One open position per symbol** (EUR and gold may both be open)
- Survives reboot via Scheduled Task `SMC-cTrader-DemoBot` (runs as `SYSTEM` at startup)

## AWS / RDP

| Item | Value |
|------|--------|
| Instance | `i-053ff91641c1c76cf` (us-east-1) |
| Public IP | `98.93.148.159` |
| User | `Administrator` |
| Password | `atas-smc-strategy/atas-smc-admin-password.txt` |
| RDP SG | `sg-0b7582a9b5e02f6a6` — your current public IP `/32` only (not 0.0.0.0/0) |

Mac Microsoft Remote Desktop → PC `98.93.148.159`.

If your ISP IP changes:
```bash
MYIP=$(curl -s https://checkip.amazonaws.com)
aws ec2 authorize-security-group-ingress --group-id sg-0b7582a9b5e02f6a6 \
  --protocol tcp --port 3389 --cidr ${MYIP}/32 --profile admin --region us-east-1
# then revoke the old /32
```

## On the VPS

Install root: `C:\smc-backtest`

| Action | Command |
|--------|---------|
| Start | `powershell -File C:\smc-backtest\scripts\windows\demo_bot_ctl.ps1 start` |
| Stop | `powershell -File C:\smc-backtest\scripts\windows\demo_bot_ctl.ps1 stop` |
| Status | `powershell -File C:\smc-backtest\scripts\windows\demo_bot_ctl.ps1 status` |
| One cycle | `powershell -File C:\smc-backtest\scripts\windows\demo_bot_ctl.ps1 once` |
| Tail logs | `powershell -File C:\smc-backtest\scripts\windows\demo_bot_ctl.ps1 logs` |

Or Task Scheduler:
```
schtasks /Run /TN SMC-cTrader-DemoBot
schtasks /End /TN SMC-cTrader-DemoBot
schtasks /Query /TN SMC-cTrader-DemoBot /V /FO LIST
```

### Log files

- `C:\smc-backtest\reports\demo_live_bot_service.log` — cycle heartbeat, entries/exits/errors
- `C:\smc-backtest\reports\demo_live_bot.jsonl` — structured signal / order events
- `C:\smc-backtest\reports\demo_executor.jsonl` — preflight dry-run decisions
- `C:\smc-backtest\reports\demo_smoke_orders.jsonl` — manual smoke fills

### Manual one-shot (PowerShell)

```powershell
cd C:\smc-backtest
$env:DEMO = "1"
python scripts\ctrader_demo_live_bot.py --once
# scan only:
python scripts\ctrader_demo_live_bot.py --once --dry-run
# gold smoke (min volume, label smc-smoke-gold):
python scripts\ctrader_demo_smoke_order.py --i-confirm-demo-order --symbol XAUUSD --label smc-smoke-gold
```

Gold smoke defaults: SL 50 / TP 100 **gold pips** (pip=0.1 → $5 / $10), not FX 15/30.

## On the Mac (source of truth)

Project: `/Users/alimire/Downloads/smc-backtest`

```bash
cd /Users/alimire/Downloads/smc-backtest
DEMO=1 .venv/bin/python scripts/ctrader_demo_live_bot.py --once --dry-run
DEMO=1 .venv/bin/python scripts/ctrader_token_refresh.py
DEMO=1 .venv/bin/python scripts/ctrader_connect_test.py
```

After code changes, re-sync the package to `C:\smc-backtest` on the VPS (S3/SSM deploy) and restart the task.

## Safety checklist

- [x] `DEMO=1` env required to start / place
- [x] `credentials/demo.json` → `"host": "demo"`
- [x] Account allowlist Fusion demo only
- [x] Max 0.25% risk, min 2R, **one position per symbol**, **max 3 open positions total** (correlation guard), 1% daily loss cap
- [x] A-tier orders labelled `smc-A`, broker minimum volume only, DEMO only
- [x] Allowlisted symbols: `EURUSD`, `XAUUSD`, `GBPUSD`, `USDJPY`, `AUDUSD`, `USDCAD`, `NZDUSD`, `USDCHF`, `USDX`
- [ ] **Do not** point credentials at live or change `host` to live

## Caveats

- A tier OOS (Jul 2026, 9 symbols, 3-fold WF): A-only n=110, E[R]=1.354, PF=3.26; combined n 387→497 (x1.28). A+ subset unchanged (asserted). See `reports/tier_study.md`.
- `research_best` multi-symbol OOS (Jul 2026): all 9 symbols cleared n≥10 / E[R]>0 / PF>1 on ~24mo cTrader M15. See `docs/RESEARCH_RESULTS.md` multi-symbol table. XAUUSD OOS: n=12, E[R]=2.854, PF=4.81. USDX (Fusion id 120) OOS: n=38, WR 26.3%, E[R]=6.30, PF=9.55 (see `reports/usdx_study.md`).
- Gold min volume on Fusion demo is `100` (0.01 lot); pip value sizing uses broker `lotSize`/`pipPosition`.
- Pending orders still block new entries globally (must be flat on pending).

## Optional later: ATAS Sim (path B)

Only if you want chart drawings again:
1. RDP in, launch ATAS, log in once (interactive)
2. Load ChartStrategy DLL, enable **Sim** orders only
3. Prefer keeping the headless DEMO bot as the execution source of truth

## Token expiry

If Open API auth fails, refresh on VPS or Mac:
```powershell
$env:DEMO=1; python C:\smc-backtest\scripts\ctrader_token_refresh.py
```
If refresh fails, re-run OAuth once on a machine with a browser: `scripts/ctrader_oauth.py`, then copy updated `credentials/demo.json` to the VPS.
