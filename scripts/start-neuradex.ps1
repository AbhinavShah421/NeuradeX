# NeuradeX startup script - launches all Docker services, prints the ngrok public
# URL, and publishes it to LIVE_URL.txt (committed to the repo so it's visible
# from anywhere). Safe to run manually or at logon/boot (see install-autostart.ps1).

param(
    [switch]$Build  # pass -Build to rebuild images before starting
)

$ErrorActionPreference = "Continue"
Set-Location (Split-Path -Parent $PSScriptRoot)   # repo root

# Scheduled tasks run with a limited system PATH that omits Docker Desktop's bin.
# Prepend the known Docker path so docker/docker compose are always resolvable.
$dockerBin = "C:\Program Files\Docker\Docker\resources\bin"
if ($env:PATH -notlike "*$dockerBin*") {
    $env:PATH = "$dockerBin;$env:PATH"
}

Write-Host ""
Write-Host "Starting NeuradeX..." -ForegroundColor Cyan

# Wait for the Docker engine - at cold boot, Docker Desktop + WSL2 can take
# several minutes. We also proactively launch Docker Desktop after 30 s in case
# Fast Startup (hybrid shutdown) left it un-started.
$dockerReady          = $false
$dockerDesktopLaunched = $false
for ($i = 0; $i -lt 120; $i++) {        # up to 10 minutes
    docker info *> $null
    if ($LASTEXITCODE -eq 0) { $dockerReady = $true; break }
    if ($i -eq 0) { Write-Host "Waiting for Docker engine..." -ForegroundColor Yellow }

    # After 30 s with no response, explicitly launch Docker Desktop.
    # Handles cold-boot / Fast Startup where the autostart may be delayed.
    if ($i -eq 6 -and -not $dockerDesktopLaunched) {
        $ddExe = "${env:ProgramFiles}\Docker\Docker\Docker Desktop.exe"
        if (Test-Path $ddExe) {
            Write-Host "  Launching Docker Desktop explicitly..." -ForegroundColor Yellow
            Start-Process $ddExe
            $dockerDesktopLaunched = $true
        }
    }
    Start-Sleep -Seconds 5
}
if (-not $dockerReady) {
    Write-Host "Docker engine not ready after 10 min - continuing anyway so VS Code still opens." -ForegroundColor Yellow
    # Do NOT exit: VS Code + Claude Code should still launch so the user can see status.
}

# Ollama runs inside Docker (stock-prediction-ollama container).
# Do NOT start host Ollama here - it would bind port 11434 and block the container.

# Bootstrap .env.ngrok so docker compose never fails on a missing env_file.
# Written only when the file is absent (e.g. fresh clone / first cold boot).
# start-ngrok.ps1 overwrites it per-attempt with the active token anyway.
$ngrokEnvFile = Join-Path (Get-Location) '.env.ngrok'
if (-not (Test-Path $ngrokEnvFile)) {
    Write-Host "  .env.ngrok not found - creating from first available token..." -ForegroundColor Yellow
    $firstToken = Get-Content '.env' |
                  Where-Object { $_ -match '^\s*NGROK_AUTHTOKEN_\d+\s*=\s*(.+?)\s*$' } |
                  ForEach-Object { $Matches[1].Trim('"').Trim("'") } |
                  Where-Object { $_ -ne '' } |
                  Select-Object -First 1
    if ($firstToken) {
        "NGROK_AUTHTOKEN=$firstToken" | Set-Content $ngrokEnvFile -Encoding ascii
        Write-Host "  .env.ngrok created. Rotation will verify the token shortly." -ForegroundColor DarkGray
    } else {
        Write-Host "  No tokens found in .env - add NGROK_AUTHTOKEN_1=<token> to enable ngrok." -ForegroundColor Yellow
        "NGROK_AUTHTOKEN=" | Set-Content $ngrokEnvFile -Encoding ascii
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
Write-Host "Starting ngrok tunnel (multi-token rotation)..." -ForegroundColor Yellow

$url = & (Join-Path $PSScriptRoot 'start-ngrok.ps1')

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

    try { "$url/neuradex" | Set-Clipboard } catch {}
    Write-Host "  (App URL copied to clipboard)" -ForegroundColor DarkGray
    Write-Host ""
} else {
    Write-Host ""
    Write-Host "All ngrok tokens failed - services are up but no public tunnel." -ForegroundColor Red
    Write-Host "Add tokens to .env as NGROK_AUTHTOKEN_1=..., NGROK_AUTHTOKEN_2=... etc." -ForegroundColor Yellow
    Write-Host "Check the inspector at http://localhost:4040" -ForegroundColor Yellow
    Write-Host ""
}

# Publish the live URL to LIVE_URL.txt and push it to the repo (visible anywhere).
& (Join-Path $PSScriptRoot 'publish-live-url.ps1')

# ─────────────────────────────────────────────────────────────────────────────
# SERVICE VERIFICATION — check all required services; retry once if anything down
# ─────────────────────────────────────────────────────────────────────────────
function Test-NeuradeXServices {
    $s = [ordered]@{}

    # Docker: any containers that exited abnormally? One-shot init containers
    # (e.g. ollama-init) exit 0 by design and must not count as failures.
    $badExits = @()
    try {
        $badExits = @(docker compose ps -a --format json 2>$null |
                      ForEach-Object { try { $_ | ConvertFrom-Json } catch {} } |
                      Where-Object { $_.State -eq 'exited' -and [int]$_.ExitCode -ne 0 })
    } catch {}
    $s["Docker containers"] = ($badExits.Count -eq 0)

    # Ollama
    try {
        Invoke-RestMethod "http://localhost:11434/api/tags" -TimeoutSec 5 -ErrorAction Stop | Out-Null
        $s["Ollama"] = $true
    } catch { $s["Ollama"] = $false }

    # ngrok HTTPS tunnel
    try {
        $t = (Invoke-RestMethod "http://localhost:4040/api/tunnels" -TimeoutSec 5 -ErrorAction Stop).tunnels |
             Where-Object { $_.proto -eq "https" } | Select-Object -First 1
        $s["ngrok tunnel"] = ($null -ne $t)
    } catch { $s["ngrok tunnel"] = $false }

    # Backend API (FastAPI)
    try {
        Invoke-WebRequest "http://localhost:8000/docs" -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop | Out-Null
        $s["Backend API"] = $true
    } catch { $s["Backend API"] = $false }

    # Frontend
    try {
        Invoke-WebRequest "http://localhost:3000" -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop | Out-Null
        $s["Frontend"] = $true
    } catch { $s["Frontend"] = $false }

    return $s
}

function Write-ServiceStatus ($health, $prefix) {
    foreach ($name in $health.Keys) {
        if ($health[$name]) {
            Write-Host ("  [OK]   $prefix$name") -ForegroundColor Green
        } else {
            Write-Host ("  [FAIL] $prefix$name") -ForegroundColor Red
        }
    }
}

Write-Host ""
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host "  Service Verification" -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan

# Brief stabilization wait — some services may still be starting
Write-Host "  Waiting 15 s for services to stabilize..." -ForegroundColor DarkGray
Start-Sleep -Seconds 15

$health = Test-NeuradeXServices
Write-ServiceStatus $health ""
$anyDown = $health.Values -contains $false

if ($anyDown) {
    Write-Host ""
    Write-Host "  Some services failed — performing clean restart..." -ForegroundColor Yellow
    Write-Host ""

    # ── Stop everything first ──────────────────────────────────────────────
    Write-Host "  Stopping all Docker containers..." -ForegroundColor Yellow
    docker compose down
    Start-Sleep -Seconds 5

    # ── Clean restart ──────────────────────────────────────────────────────
    # Kill any host-level Ollama process that may be squatting port 11434
    # (Docker now runs Ollama; a host Ollama would block the container's port bind).
    $ollamaProcs = Get-Process "ollama" -ErrorAction SilentlyContinue
    if ($ollamaProcs) {
        Write-Host "  Stopping host Ollama (port 11434 must be free for Docker)..." -ForegroundColor Yellow
        $ollamaProcs | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 3
    }

    Write-Host "  Starting all Docker containers..." -ForegroundColor Yellow
    docker compose up -d

    # Allow services time to fully come up
    Write-Host "  Waiting 30 s for services to stabilize..." -ForegroundColor DarkGray
    Start-Sleep -Seconds 30

    # ── Re-check ───────────────────────────────────────────────────────────
    Write-Host ""
    Write-Host "  Re-check results:" -ForegroundColor Cyan
    $health2 = Test-NeuradeXServices
    Write-ServiceStatus $health2 ""

    if ($health2.Values -contains $false) {
        Write-Host ""
        Write-Host "  WARNING: Some services are still down after clean restart." -ForegroundColor Red
        Write-Host "  Check logs with: docker compose logs --tail=50 <service-name>" -ForegroundColor DarkGray
    } else {
        Write-Host ""
        Write-Host "  All services recovered successfully." -ForegroundColor Green
    }
} else {
    Write-Host ""
    Write-Host "  All services are healthy." -ForegroundColor Green
}

Write-Host "=============================================" -ForegroundColor Cyan
Write-Host ""

# Open VS Code with this project.
$repoRoot = Split-Path -Parent $PSScriptRoot
$codeCli  = "$env:LOCALAPPDATA\Programs\Microsoft VS Code\bin\code.cmd"
$codeExe  = "$env:LOCALAPPDATA\Programs\Microsoft VS Code\Code.exe"

if (Test-Path $codeCli) {
    Start-Process -FilePath $codeCli -ArgumentList "`"$repoRoot`""
} elseif (Test-Path $codeExe) {
    Start-Process -FilePath $codeExe -ArgumentList "`"$repoRoot`""
} else {
    try { Start-Process "code" -ArgumentList "`"$repoRoot`"" } catch {}
}

# Focus Claude Code and submit /remote-control via keyboard automation only.
# We do NOT use "code --command" here: on this system it has no working IPC
# socket and silently falls back to launching a fresh blank VS Code window
# for every --command call (visible as "not in the list of known options"
# Electron warnings). Pure SendKeys needs no IPC at all.
Write-Host "Waiting for VS Code + Claude Code extension to initialize..." -ForegroundColor Yellow

Add-Type -AssemblyName System.Windows.Forms

# Wait for a VS Code window to appear (up to 30 s)
$codeWin = $null
$elapsed = 0
while (-not $codeWin -and $elapsed -lt 30) {
    Start-Sleep -Seconds 2; $elapsed += 2
    $codeWin = Get-Process "Code" -ErrorAction SilentlyContinue |
               Where-Object { $_.MainWindowHandle -ne 0 } |
               Select-Object -First 1
}

if (-not $codeWin) {
    Write-Host "VS Code window not found. Open Claude Code manually and run /remote-control." -ForegroundColor Yellow
} else {
    # AttachThreadInput bypasses Windows focus-stealing prevention.
    # Plain SetForegroundWindow fails when pwsh runs inside Windows Terminal
    # because wt.exe (not pwsh.exe) owns the foreground lock.
    try {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public class NxFocus {
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll")] static extern bool AttachThreadInput(uint id, uint to, bool attach);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();
    public static void ForceForeground(IntPtr hwnd) {
        ShowWindow(hwnd, 9);
        IntPtr fg = GetForegroundWindow();
        uint fgTid = GetWindowThreadProcessId(fg, out uint _p);
        uint myTid = GetCurrentThreadId();
        if (fgTid != myTid) AttachThreadInput(myTid, fgTid, true);
        SetForegroundWindow(hwnd);
        if (fgTid != myTid) AttachThreadInput(myTid, fgTid, false);
    }
}
'@ -ErrorAction Stop
    } catch { }

    $hwnd = $codeWin.MainWindowHandle

    # Wait until VS Code's window title contains "Visual Studio Code" — that string
    # only appears once the workspace and extensions have fully loaded. The window
    # handle exists almost immediately at launch, so we can't rely on that alone.
    Write-Host "  Waiting for VS Code to finish loading (title check, up to 60 s)..." -ForegroundColor DarkGray
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 2
        $loadedWin = Get-Process "Code" -ErrorAction SilentlyContinue |
                     Where-Object { $_.MainWindowTitle -like "*Visual Studio Code*" -and $_.MainWindowHandle -ne 0 } |
                     Select-Object -First 1
        if ($loadedWin) {
            $codeWin = $loadedWin
            $hwnd    = $codeWin.MainWindowHandle
            Write-Host "  VS Code loaded ($($loadedWin.MainWindowTitle))." -ForegroundColor DarkGray
            break
        }
    }
    # Extra buffer so Claude Code extension fully initialises after the window is ready
    Start-Sleep -Seconds 5

    Write-Host "  Opening Claude Code chat via command palette..." -ForegroundColor DarkGray

    # Bring VS Code to foreground
    try { [NxFocus]::ForceForeground($hwnd) } catch {}
    Start-Sleep -Milliseconds 1500

    # Open command palette (Ctrl+Shift+P) and run the Claude Code focus command.
    # Use clipboard paste instead of SendKeys character-by-character: SendKeys drops
    # or reorders characters when the system is under load (Docker/VS Code just started).
    Set-Clipboard "Claude Code: Focus on Claude Code View"
    [System.Windows.Forms.SendKeys]::SendWait("^+p")
    Start-Sleep -Milliseconds 1000
    [System.Windows.Forms.SendKeys]::SendWait("^v")   # paste — atomic, order guaranteed
    Start-Sleep -Milliseconds 800
    [System.Windows.Forms.SendKeys]::SendWait("{ENTER}")
    Start-Sleep -Milliseconds 3000

    # Re-focus the VS Code window after the command palette closes
    try { [NxFocus]::ForceForeground($hwnd) } catch {}
    Start-Sleep -Milliseconds 800

    # Close any remaining command palette UI so the chat input is active
    [System.Windows.Forms.SendKeys]::SendWait("{ESC}")
    Start-Sleep -Milliseconds 300

    # Paste and submit /remote-control
    Set-Clipboard "/remote-control"
    [System.Windows.Forms.SendKeys]::SendWait("^v")
    Start-Sleep -Milliseconds 300
    [System.Windows.Forms.SendKeys]::SendWait("{ENTER}")

    Write-Host "Claude Code /remote-control submitted" -ForegroundColor Green
}

exit 0
