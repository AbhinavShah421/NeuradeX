# NeuradeX Resource Mode Switcher
# Usage: .\scripts\neuradex-mode.ps1 -Mode <default|market|train>
#
# default  — right-sized baseline (all 27 containers, balanced limits)
# market   — boosts trading services, throttles ML training (use 9:15–15:30 IST)
# train    — maximises model-trainer + rl-agent (use nights/weekends)

param(
    [Parameter(Mandatory=$true)]
    [ValidateSet("default", "market", "train")]
    [string]$Mode
)

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

switch ($Mode) {
    "default" {
        Write-Host "[NeuradeX] Switching to DEFAULT mode (balanced baseline)..." -ForegroundColor Cyan
        docker compose -f docker-compose.yml up -d --no-recreate
    }
    "market" {
        Write-Host "[NeuradeX] Switching to MARKET HOURS mode (trading-optimised)..." -ForegroundColor Green
        Write-Host "  model-trainer CPU: 1.0 -> 0.2  |  backend CPU: 0.75 -> 1.0"
        docker compose -f docker-compose.yml -f docker-compose.market.yml up -d
    }
    "train" {
        Write-Host "[NeuradeX] Switching to TRAINING mode (ML-optimised)..." -ForegroundColor Magenta
        Write-Host "  model-trainer: 1024M/1.0 -> 2048M/2.0  |  rl-agent: -> 768M/1.0"
        docker compose -f docker-compose.yml -f docker-compose.train.yml up -d
    }
}

Write-Host ""
Write-Host "[NeuradeX] Mode applied: $Mode" -ForegroundColor Yellow
Write-Host "Check resource usage: docker stats --no-stream"
