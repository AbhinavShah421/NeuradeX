# Runs the NeuradeX backend test suite inside the backend container (where the
# app and all its deps already live). Copies backend/tests into the container
# fresh each run, then invokes pytest. Any extra args are passed straight to
# pytest, e.g.:
#
#   .\scripts\run-tests.ps1                 # everything (unit + integration)
#   .\scripts\run-tests.ps1 -k recordings   # only recordings tests
#   .\scripts\run-tests.ps1 -m "not integration"   # unit tests only
#
param(
    [string]$Container = "stock-prediction-backend"
)

$ErrorActionPreference = "Stop"

# Scheduled/limited shells may omit Docker Desktop's bin from PATH.
$dockerBin = "C:\Program Files\Docker\Docker\resources\bin"
if ($env:PATH -notlike "*$dockerBin*") { $env:PATH = "$dockerBin;$env:PATH" }

$repo      = Split-Path -Parent $PSScriptRoot
$testsDir  = Join-Path $repo "backend\tests"

if (-not (Test-Path $testsDir)) {
    Write-Host "No tests directory found at $testsDir" -ForegroundColor Red
    exit 1
}

# Is the target container running?
$running = docker ps --filter "name=$Container" --filter "status=running" --format "{{.Names}}"
if (-not $running) {
    Write-Host "Container '$Container' is not running. Start the stack first (scripts/start-neuradex.ps1)." -ForegroundColor Red
    exit 1
}

Write-Host "Copying tests into $Container..." -ForegroundColor Cyan
docker exec $Container rm -rf /app/tests | Out-Null
docker cp $testsDir "${Container}:/app/tests"

Write-Host "Running pytest..." -ForegroundColor Cyan
docker exec -e PYTHONPATH=/app $Container python -m pytest /app/tests @args
$code = $LASTEXITCODE

if ($code -eq 0) {
    Write-Host "`nAll tests passed." -ForegroundColor Green
} else {
    Write-Host "`nTests failed (exit $code)." -ForegroundColor Red
}
exit $code
