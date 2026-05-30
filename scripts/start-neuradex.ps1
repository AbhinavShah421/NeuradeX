# NeuradeX startup script — launches all Docker services and prints the ngrok public URL

param(
    [switch]$Build  # pass -Build to rebuild images before starting
)

$ErrorActionPreference = "Continue"

Write-Host ""
Write-Host "Starting NeuradeX..." -ForegroundColor Cyan

if ($Build) {
    Write-Host "Building images..." -ForegroundColor Yellow
    docker compose up -d --build
} else {
    docker compose up -d
}

if ($LASTEXITCODE -ne 0) {
    Write-Host "docker compose failed. Check the output above." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Waiting for ngrok tunnel..." -ForegroundColor Yellow

$url    = $null
$waited = 0

while (-not $url -and $waited -lt 60) {
    Start-Sleep -Seconds 2
    $waited += 2
    try {
        $resp = Invoke-RestMethod "http://localhost:4040/api/tunnels" -ErrorAction Stop
        $t    = $resp.tunnels | Where-Object { $_.proto -eq "https" } | Select-Object -First 1
        if ($t) { $url = $t.public_url }
    } catch {}
}

if ($url) {
    Write-Host ""
    Write-Host "=============================================" -ForegroundColor Green
    Write-Host "  NeuradeX is live!" -ForegroundColor Green
    Write-Host "=============================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "  App       $url/neuradex" -ForegroundColor White
    Write-Host "  API Docs  $url/neuradex/backend/docs" -ForegroundColor White
    Write-Host "  Docs Site $url/neuradex/docs/docs" -ForegroundColor White
    Write-Host ""
    Write-Host "  Local access (unchanged):" -ForegroundColor DarkGray
    Write-Host "  Frontend  http://localhost:3000" -ForegroundColor DarkGray
    Write-Host "  Backend   http://localhost:8000/docs" -ForegroundColor DarkGray
    Write-Host "  Docs      http://localhost:3001/neuradex/docs" -ForegroundColor DarkGray
    Write-Host "  Inspector http://localhost:4040" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "=============================================" -ForegroundColor Green

    # Copy app URL to clipboard silently
    try { $url + "/neuradex" | Set-Clipboard } catch {}

    Write-Host "  (App URL copied to clipboard)" -ForegroundColor DarkGray
    Write-Host ""
} else {
    Write-Host ""
    Write-Host "Timed out waiting for ngrok. Services are running but tunnel URL unknown." -ForegroundColor Yellow
    Write-Host "Check the inspector at http://localhost:4040" -ForegroundColor Yellow
    Write-Host ""
}
