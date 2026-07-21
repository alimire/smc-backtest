# Start / stop / status helpers for SMC DEMO bot

param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("start", "stop", "status", "once", "logs")]
    [string]$Action
)

$TaskName = "SMC-cTrader-DemoBot"
$Root = "C:\smc-backtest"
$Python = @(
    "C:\Python312\python.exe",
    "C:\Program Files\Python312\python.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Python) {
    $Python = (Get-Command python -ErrorAction SilentlyContinue).Source
}

switch ($Action) {
    "start" {
        schtasks /Run /TN $TaskName
        Write-Host "Started $TaskName (DEMO=1)"
    }
    "stop" {
        schtasks /End /TN $TaskName
        Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
            Where-Object { $_.CommandLine -match "ctrader_demo_live_bot" } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Write-Host "Stopped $TaskName"
    }
    "status" {
        schtasks /Query /TN $TaskName /V /FO LIST
        Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
            Where-Object { $_.CommandLine -match "ctrader_demo_live_bot" } |
            Select-Object ProcessId, CommandLine
    }
    "once" {
        Set-Location $Root
        $env:DEMO = "1"
        & $Python scripts\ctrader_demo_live_bot.py --once
    }
    "logs" {
        Write-Host "=== service log (tail) ==="
        Get-Content (Join-Path $Root "reports\demo_live_bot_service.log") -Tail 80 -ErrorAction SilentlyContinue
        Write-Host "=== events (tail) ==="
        Get-Content (Join-Path $Root "reports\demo_live_bot.jsonl") -Tail 20 -ErrorAction SilentlyContinue
    }
}
