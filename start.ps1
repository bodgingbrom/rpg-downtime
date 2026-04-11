# Downtime Derby - Dev Startup Script
# Double-click or run from PowerShell to (re)start the bot.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# Kill any existing bot process
Get-Process python -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*bot.py*" } |
    Stop-Process -Force -ErrorAction SilentlyContinue

Write-Host "Starting Downtime Derby..." -ForegroundColor Cyan

# Activate venv if it exists
if (Test-Path ".\venv\Scripts\Activate.ps1") {
    & .\venv\Scripts\Activate.ps1
}

python bot.py
