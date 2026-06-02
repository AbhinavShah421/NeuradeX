# Publish the live ngrok URL.
# Reads the current public URL from the ngrok agent API, writes it to
# LIVE_URL.txt (overwriting the previous content), and commits + pushes it so the
# URL is visible from anywhere on GitHub. Only commits when the URL changed.

$ErrorActionPreference = 'Continue'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

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
if (-not $publicUrl) { Write-Host "[publish] ngrok URL not available yet"; exit 1 }

# Idempotency: if the live URL hasn't changed, do nothing (the file carries a
# timestamp, so only gate on the URL itself to avoid churn / no-op commits).
$file = Join-Path $repo 'LIVE_URL.txt'
if ((Test-Path $file) -and ((Get-Content $file -Raw) -match [regex]::Escape($publicUrl))) {
    Write-Host "[publish] URL unchanged ($publicUrl), nothing to do"
    exit 0
}

# 2. Overwrite LIVE_URL.txt with ONLY the new, live URL.
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
Write-Host "[publish] $publicUrl"

# 3. Commit + push the new live URL.
git add LIVE_URL.txt 2>&1 | Out-Null
git commit -m "chore: live url $publicUrl" 2>&1 | Out-Null
git push 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    git pull --rebase --autostash 2>&1 | Out-Null
    git push 2>&1 | Out-Null
}
Write-Host "[publish] committed and pushed"
