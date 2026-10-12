<#
Starts the whole local setup with two consoles:

  this console   BACK  — both bots (testnet + real), output tagged [TESTNET] / [BINANCE]
  new window     FRONT — the dashboard (http://localhost:8501), opened in the browser

  .\start_all.ps1                      # everything
  .\start_all.ps1 -NoProduction        # skip the real-money bot
  .\start_all.ps1 -NoTestnet           # skip the testnet bot
  .\start_all.ps1 -NoDashboard         # bots only
  .\start_all.cmd                      # same, double-clickable (bypasses the script policy)

The real-money bot starts PAUSED; activate it from the dashboard. Running it twice is safe:
each bot refuses to start if it's already running (execution/process_lock.py), and the
dashboard is skipped if port 8501 is in use.
#>
param(
    [switch]$NoTestnet,
    [switch]$NoProduction,
    [switch]$NoDashboard
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$port = 8501
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8   # block characters in the banners
$env:PYTHONIOENCODING = "utf-8"
Set-Location -LiteralPath $root

if (-not $NoDashboard) {
    $busy = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($busy) {
        Write-Host "Dashboard: ya estaba corriendo en http://localhost:$port" -ForegroundColor Cyan
    } else {
        $front = "`$Host.UI.RawUI.WindowTitle = 'FRONT · Dashboard'; " +
                 "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; `$env:PYTHONIOENCODING = 'utf-8'; " +
                 "Set-Location -LiteralPath '$root'; python -m xrp_futures.scripts.banner front; " +
                 "streamlit run xrp_futures/monitor/dashboard.py --server.port $port --server.headless true"
        Start-Process powershell -WorkingDirectory $root `
            -ArgumentList @("-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $front)
        Write-Host "Dashboard: abierto en una ventana nueva (FRONT)" -ForegroundColor Cyan
        Start-Sleep -Seconds 6
    }
    Start-Process "http://localhost:$port"
}

if ($NoTestnet -and $NoProduction) {
    Write-Host "Sin bots que iniciar." ; return
}
$Host.UI.RawUI.WindowTitle = "BACK · Bots de trading"
$botArgs = @()
if ($NoTestnet) { $botArgs += "--no-testnet" }
if ($NoProduction) { $botArgs += "--no-production" }
python -m xrp_futures.scripts.run_bots @botArgs
