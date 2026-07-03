# Manages ngrok token rotation across multiple accounts stored in the root .env.
# For each NGROK_AUTHTOKEN_N entry (in order):
#   1. Writes NGROK_AUTHTOKEN=<token> to .env.ngrok (read by docker compose)
#   2. Stops/removes the existing ngrok container
#   3. Starts a fresh container and watches for: tunnel URL or auth failure exit
#   4. On tunnel URL → verifies reachability, accepts and returns the URL
#   5. On container exit or timeout → tries the next token
#
# Usage (standalone or called from start-neuradex.ps1):
#   .\scripts\start-ngrok.ps1
#   $url = & .\scripts\start-ngrok.ps1   # captures the URL; status goes to console

param(
    [int]$TimeoutPerToken = 90   # seconds to wait per token (covers reconnection delay)
)

$ErrorActionPreference = 'Continue'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$dockerBin = "C:\Program Files\Docker\Docker\resources\bin"
if ($env:PATH -notlike "*$dockerBin*") { $env:PATH = "$dockerBin;$env:PATH" }

# ── Read tokens from root .env ────────────────────────────────────────────────
$envFile = Join-Path $repo '.env'
$tokens  = [System.Collections.Generic.List[string]]::new()

if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*NGROK_AUTHTOKEN_\d+\s*=\s*(.+?)\s*$') {
            $val = $Matches[1].Trim('"').Trim("'")
            if ($val) { $tokens.Add($val) }
        }
    }
}

if ($tokens.Count -eq 0) {
    Write-Host "[ngrok] ERROR: No NGROK_AUTHTOKEN_N entries found in .env" -ForegroundColor Red
    Write-Host "[ngrok]   Add tokens as: NGROK_AUTHTOKEN_1=<token>  NGROK_AUTHTOKEN_2=<token> ..." -ForegroundColor Yellow
    exit 1
}

Write-Host "[ngrok] $($tokens.Count) token(s) found." -ForegroundColor Cyan

# ── Helpers ───────────────────────────────────────────────────────────────────

# Write the active token to .env.ngrok so docker compose picks it up.
# .env.ngrok is git-ignored via the .env.* pattern.
function Set-NgrokToken {
    param([string]$Token)
    "NGROK_AUTHTOKEN=$Token" | Set-Content (Join-Path $repo '.env.ngrok') -Encoding ascii
}

function Reset-NgrokContainer {
    Write-Host "[ngrok]   Stopping existing container..." -ForegroundColor DarkGray
    docker compose stop  ngrok  2>$null | Out-Null
    docker compose rm -f ngrok  2>$null | Out-Null
}

function Wait-NgrokTunnel {
    param([int]$TimeoutSec)
    $elapsed = 0
    while ($elapsed -lt $TimeoutSec) {
        Start-Sleep -Seconds 2
        $elapsed += 2

        # Early-exit: auth failure causes the container to exit with code 1.
        # With restart:"no" it stays exited - no point waiting the full timeout.
        try {
            $state = (docker inspect stock-prediction-ngrok --format "{{.State.Status}}" 2>$null).Trim()
            if ($state -eq "exited") {
                Write-Host "[ngrok]   Container exited - auth rejected." -ForegroundColor Red
                return $null
            }
        } catch {}

        try {
            $resp = Invoke-RestMethod "http://localhost:4040/api/tunnels" -TimeoutSec 4 -ErrorAction Stop
            $t    = $resp.tunnels | Where-Object { $_.proto -eq "https" } | Select-Object -First 1
            if ($t) { return $t.public_url }
        } catch {}
    }
    return $null
}

function Test-TunnelReachable {
    param([string]$Url)
    # ngrok sets Ngrok-Error-Code on responses it intercepts.
    # ERR_NGROK_6024 = free-tier browser interstitial — tunnel is fine, skip it.
    # All other ngrok error codes (rate limit 727, expired account, etc.) = skip token.
    $fatalNgrokErrors = @("ERR_NGROK_727","ERR_NGROK_108","ERR_NGROK_302","ERR_NGROK_3004")
    function Get-NgrokErrorCode($headers) {
        if (-not $headers) { return $null }
        try { return $headers["Ngrok-Error-Code"] } catch { return $null }
    }
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop
        $errCode = Get-NgrokErrorCode $r.Headers
        if ($errCode -and $fatalNgrokErrors -contains $errCode) { return $false }
        return ($r.StatusCode -lt 502)
    } catch {
        $resp = $_.Exception.Response
        if (-not $resp) { return $false }
        $errCode = Get-NgrokErrorCode $resp.Headers
        if ($errCode -and $fatalNgrokErrors -contains $errCode) { return $false }
        $code = $resp.StatusCode.value__
        # 4xx from our own nginx/app = tunnel is routing traffic correctly
        return ($code -ge 400 -and $code -lt 502)
    }
}

# ── Hot-boot fast-path: reuse the existing tunnel if it is still healthy ─────
# Avoids an unnecessary stop/recreate cycle when the previous session's ngrok
# is still running and the token has not hit any rate limit.

Write-Host "[ngrok] Checking existing tunnel..." -ForegroundColor Cyan
$activeUrl = $null

try {
    $cState = (docker inspect stock-prediction-ngrok --format "{{.State.Status}}" 2>$null).Trim()
    if ($cState -eq "running") {
        $resp = Invoke-RestMethod "http://localhost:4040/api/tunnels" -TimeoutSec 5 -ErrorAction Stop
        $existing = $resp.tunnels | Where-Object { $_.proto -eq "https" } | Select-Object -First 1
        if ($existing) {
            Write-Host "[ngrok] Found active tunnel: $($existing.public_url)" -ForegroundColor DarkGray
            Write-Host "[ngrok] Verifying..." -ForegroundColor DarkGray
            if (Test-TunnelReachable -Url $existing.public_url) {
                Write-Host "[ngrok] Tunnel is healthy - skipping rotation." -ForegroundColor Green
                $activeUrl = $existing.public_url
            } else {
                Write-Host "[ngrok] Tunnel unhealthy (rate-limited or blocked). Starting rotation..." -ForegroundColor Yellow
            }
        }
    }
} catch {}

# ── Token rotation loop ───────────────────────────────────────────────────────

if (-not $activeUrl) {

    Write-Host "[ngrok] Starting rotation through $($tokens.Count) token(s)..." -ForegroundColor Cyan

    for ($i = 0; $i -lt $tokens.Count; $i++) {
        $token  = $tokens[$i]
        $masked = $token.Substring(0, [Math]::Min(10, $token.Length)) + "..."

        Write-Host ""
        Write-Host "[ngrok] -- Token $($i + 1) / $($tokens.Count)  ($masked) --" -ForegroundColor Yellow

        Set-NgrokToken -Token $token
        Reset-NgrokContainer

        # Route docker compose output to Write-Host so it goes to the console,
        # not the success stream — keeps $url = & start-ngrok.ps1 returning only the URL.
        docker compose up -d ngrok 2>&1 | ForEach-Object { Write-Host $_ }

        Write-Host "[ngrok]   Waiting for tunnel (up to ${TimeoutPerToken}s, exits early on auth failure)..." -ForegroundColor DarkGray
        $url = Wait-NgrokTunnel -TimeoutSec $TimeoutPerToken

        if (-not $url) {
            Write-Host "[ngrok]   Token $($i + 1) failed." -ForegroundColor Red
            continue
        }

        Write-Host "[ngrok]   Tunnel up: $url" -ForegroundColor DarkGray
        Write-Host "[ngrok]   Verifying live URL..." -ForegroundColor DarkGray

        if (Test-TunnelReachable -Url $url) {
            Write-Host "[ngrok]   Reachable. Token $($i + 1) accepted." -ForegroundColor Green
            $activeUrl = $url
            break
        } else {
            Write-Host "[ngrok]   Tunnel up but URL unreachable through it - skipping token." -ForegroundColor Red
        }
    }
}  # end if (-not $activeUrl)

Write-Host ""
if (-not $activeUrl) {
    Write-Host "[ngrok] All $($tokens.Count) token(s) exhausted. No live tunnel available." -ForegroundColor Red
    Write-Host "[ngrok] Add more tokens to .env as NGROK_AUTHTOKEN_4=... etc." -ForegroundColor Yellow
    exit 1
}

Write-Host "[ngrok] =============================================" -ForegroundColor Green
Write-Host "[ngrok] Active tunnel: $activeUrl" -ForegroundColor Green
Write-Host "[ngrok] =============================================" -ForegroundColor Green

# Emit the URL on the success stream so callers can capture it.
$activeUrl
exit 0
