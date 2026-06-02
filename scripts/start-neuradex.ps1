# NeuradeX startup script - launches all Docker services, prints the ngrok public
# URL, and publishes it to LIVE_URL.txt (committed to the repo so it's visible
# from anywhere). Safe to run manually or at logon/boot (see install-autostart.ps1).

param(
    [switch]$Build  # pass -Build to rebuild images before starting
)

$ErrorActionPreference = "Continue"
Set-Location (Split-Path -Parent $PSScriptRoot)   # repo root

Write-Host ""
Write-Host "Starting NeuradeX..." -ForegroundColor Cyan

# Wait for the Docker engine - at boot, Docker Desktop may still be starting.
$dockerReady = $false
for ($i = 0; $i -lt 60; $i++) {
    docker info *> $null
    if ($LASTEXITCODE -eq 0) { $dockerReady = $true; break }
    if ($i -eq 0) { Write-Host "Waiting for Docker engine..." -ForegroundColor Yellow }
    Start-Sleep -Seconds 5
}
if (-not $dockerReady) {
    Write-Host "Docker engine not ready. Enable 'Start Docker Desktop when you log in'." -ForegroundColor Red
    exit 1
}

# ── Start Ollama if not already running ─────────────────────────────────────
$ollamaReady = $false
try {
    Invoke-RestMethod "http://localhost:11434/api/tags" -ErrorAction Stop | Out-Null
    $ollamaReady = $true
    Write-Host "Ollama already running." -ForegroundColor DarkGray
} catch {}

if (-not $ollamaReady) {
    Write-Host "Starting Ollama..." -ForegroundColor Yellow
    $ollamaExe = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
    Start-Process -FilePath $ollamaExe -ArgumentList "serve" -WindowStyle Hidden
    for ($i = 0; $i -lt 15; $i++) {
        Start-Sleep -Seconds 2
        try {
            Invoke-RestMethod "http://localhost:11434/api/tags" -ErrorAction Stop | Out-Null
            $ollamaReady = $true
            Write-Host "Ollama ready." -ForegroundColor Green
            break
        } catch {}
    }
    if (-not $ollamaReady) {
        Write-Host "Warning: Ollama did not start — LLM will fall back to Anthropic or be unavailable." -ForegroundColor Yellow
    }
}

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

while (-not $url -and $waited -lt 90) {
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

# Publish the live URL to LIVE_URL.txt and push it to the repo (visible anywhere).
& (Join-Path $PSScriptRoot 'publish-live-url.ps1')
