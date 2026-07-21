# Install SMC research_best DEMO bot on Windows VPS.
# DEMO ONLY — Fusion Markets demo account. Never enables live trading.

$ErrorActionPreference = "Stop"
$Root = "C:\smc-backtest"
$Python = "C:\Python312\python.exe"
$TaskName = "SMC-cTrader-DemoBot"

Write-Host "=== SMC DEMO bot install ===" -ForegroundColor Cyan
Write-Host "This installs a DEMO-ONLY headless cTrader bot (research_best)."

# Python via Chocolatey if missing
if (-not (Test-Path $Python)) {
    $py = Get-Command python -ErrorAction SilentlyContinue
    if ($py) {
        $Python = $py.Source
    } else {
        Write-Host "Installing Python 3.12 via Chocolatey..."
        choco install python312 -y --no-progress
        refreshenv 2>$null
        if (Test-Path "C:\Python312\python.exe") {
            $Python = "C:\Python312\python.exe"
        } elseif (Test-Path "C:\Program Files\Python312\python.exe") {
            $Python = "C:\Program Files\Python312\python.exe"
        } else {
            $Python = (Get-Command python).Source
        }
    }
}
Write-Host "Python: $Python"
& $Python --version

if (-not (Test-Path $Root)) {
    throw "Missing $Root — deploy package first"
}

Set-Location $Root
& $Python -m pip install --upgrade pip
& $Python -m pip install -r requirements.txt

# Ensure DEMO gate file for Task Scheduler
$EnvFile = Join-Path $Root "demo.env.cmd"
@"
@echo off
set DEMO=1
"@ | Set-Content -Path $EnvFile -Encoding ASCII

$Runner = Join-Path $Root "scripts\windows\run_demo_bot.cmd"
New-Item -ItemType Directory -Force -Path (Split-Path $Runner) | Out-Null
@"
@echo off
cd /d C:\smc-backtest
set DEMO=1
"$Python" scripts\ctrader_demo_live_bot.py >> reports\demo_live_bot_service.log 2>&1
"@ | Set-Content -Path $Runner -Encoding ASCII

# Scheduled task: SYSTEM at startup (works under SSM; survives reboot)
cmd /c "schtasks /Delete /TN $TaskName /F" | Out-Null
cmd /c "schtasks /Create /TN $TaskName /SC ONSTART /RU SYSTEM /RL HIGHEST /F /TR `"$Runner`""


Write-Host "Registered scheduled task: $TaskName"
Write-Host "Start:  schtasks /Run /TN $TaskName"
Write-Host "Stop:   schtasks /End /TN $TaskName"
Write-Host "Logs:   $Root\reports\demo_live_bot_service.log"
Write-Host "Events: $Root\reports\demo_live_bot.jsonl"
Write-Host "DONE — DEMO ONLY" -ForegroundColor Green
