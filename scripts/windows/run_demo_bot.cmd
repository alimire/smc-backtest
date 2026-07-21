@echo off
cd /d C:\smc-backtest
set DEMO=1
REM Python path filled by install_demo_bot.ps1; fallback to PATH
where python >nul 2>&1
if %ERRORLEVEL%==0 (
  python scripts\ctrader_demo_live_bot.py
) else if exist C:\Python312\python.exe (
  C:\Python312\python.exe scripts\ctrader_demo_live_bot.py
) else (
  "C:\Program Files\Python312\python.exe" scripts\ctrader_demo_live_bot.py
)
