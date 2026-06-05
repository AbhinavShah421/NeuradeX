# Publish the live ngrok URL.
# Reads the current public URL from the ngrok agent API, writes it to
# LIVE_URL.txt (overwriting the previous content), and commits + pushes it so the
# URL is visible from anywhere on GitHub. Only commits when the URL changed.

$ErrorActionPreference = 'Continue'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

# Ensure git is in PATH — scheduled tasks get a stripped environment.
$gitPaths = @(
    "$env:ProgramFiles\Git\cmd",
    "$env:ProgramFiles\Git\bin",
    "${env:ProgramFiles(x86)}\Git\cmd"
)
foreach ($p in $gitPaths) {
    if ((Test-Path $p) -and ($env:PATH -notlike "*$p*")) {
        $env:PATH = "$p;$env:PATH"
    }
}

$logFile = Join-Path $repo 'scripts\publish-live-url.log'
function Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    Write-Host $line
    Add-Content -Path $logFile -Value $line -Encoding utf8
}

# 1. Wait for ngrok's local API and a public https URL (up to ~90s).
$publicUrl = $null
for ($i = 0; $i -lt 45; $i++) {
    try {
        $resp = Invoke-RestMethod -Uri 'http://localhost:4040/api/tunnels' -TimeoutSec 5
        $t = $resp.tunnels | Where-Object { $_.public_url -like 'https*' } | Select-Object -First 1
        if ($t) { $publicUrl = $t.public_url; break }
    } catch { }
    Start-Sleep -Seconds 2
}
if (-not $publicUrl) { Log '[publish] ngrok URL not available yet'; exit 1 }

# Idempotency: if the live URL hasn't changed, do nothing.
$file = Join-Path $repo 'LIVE_URL.txt'
if ((Test-Path $file) -and ((Get-Content $file -Raw) -match [regex]::Escape($publicUrl))) {
    Log "[publish] URL unchanged ($publicUrl), nothing to do"
    exit 0
}

# 2. Overwrite LIVE_URL.txt with the new live URL.
$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz'
$lines = @(
    'NeuradeX is LIVE',
    '',
    $publicUrl,
    '',
    "Dashboard: $publicUrl/neuradex",
    "Updated:   $stamp"
)
Set-Content -Path $file -Value $lines -Encoding ascii
Log "[publish] $publicUrl"

# 3. Commit + push the new live URL.
$gitOut = git add LIVE_URL.txt 2>&1; Log "[git add] $gitOut"
$gitOut = git commit -m "chore: live url $publicUrl" 2>&1; Log "[git commit] $gitOut"
$gitOut = git push 2>&1; Log "[git push] $gitOut"
if ($LASTEXITCODE -ne 0) {
    Log "[publish] push failed (exit $LASTEXITCODE), retrying after rebase..."
    $gitOut = git pull --rebase --autostash 2>&1; Log "[git pull] $gitOut"
    $gitOut = git push 2>&1; Log "[git push retry] $gitOut"
    if ($LASTEXITCODE -ne 0) {
        Log "[publish] ERROR: push failed after retry (exit $LASTEXITCODE)"
        exit 1
    }
}
Log "[publish] committed and pushed successfully"
