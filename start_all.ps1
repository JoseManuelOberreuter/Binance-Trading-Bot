<#
Starts the whole local setup in separate windows (one per process, so each keeps its own
log and can be stopped on its own with Ctrl+C):

  Bot testnet          test money
  Bot Binance (real)   real money — starts PAUSED; activate it from the dashboard
  Dashboard            http://localhost:8501 (opened in the browser)

  .\start_all.ps1                      # everything
  .\start_all.ps1 -NoProduction        # skip the real-money bot
  .\start_all.ps1 -NoTestnet           # skip the testnet bot
  .\start_all.cmd                      # same, double-clickable (bypasses the script policy)

Running it twice is safe: each bot refuses to start if it's already running
(execution/process_lock.py), and the dashboard is skipped if port 8501 is in use.
#>
param(
    [switch]$NoTestnet,
    [switch]$NoProduction,
    [switch]$NoDashboard
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$port = 8501

function Start-Window([string]$title, [string]$command) {
    $full = "`$Host.UI.RawUI.WindowTitle = '$title'; Set-Location -LiteralPath '$root'; $command"
    Start-Process powershell -WorkingDirectory $root `
        -ArgumentList @("-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $full)
    Write-Host "  abierto: $title"
}

Write-Host "Iniciando en $root"

if (-not $NoTestnet) {
    Start-Window "Bot testnet" "`$env:FUTURES_ENVIRONMENT = 'testnet'; python -m xrp_futures.scripts.run_live_bot"
}

if (-not $NoProduction) {
    Start-Window "Bot Binance (real)" ("`$env:FUTURES_ENVIRONMENT = 'production'; " +
        "python -m xrp_futures.scripts.run_live_bot --allow-production")
}

if (-not $NoDashboard) {
    $busy = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($busy) {
        Write-Host "  dashboard: ya estaba corriendo en el puerto $port"
    } else {
        Start-Window "Dashboard" "streamlit run xrp_futures/monitor/dashboard.py --server.port $port --server.headless true"
        Start-Sleep -Seconds 6
    }
    Start-Process "http://localhost:$port"
}

Write-Host ""
Write-Host "Listo. El bot real arranca en pausa: actívalo en el dashboard (vista Binance > Activar bot)."
Write-Host "No suspendas el PC mientras los bots corren."
