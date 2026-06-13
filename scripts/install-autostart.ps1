# Register NeuradeX to auto-start at logon.
# Creates a Scheduled Task that runs start-neuradex.ps1 every time you log in,
# which brings the stack up and publishes the live ngrok URL to the repo.
#
# Run once:   powershell -ExecutionPolicy Bypass -File scripts\install-autostart.ps1
# Remove:     Unregister-ScheduledTask -TaskName 'NeuradeX-AutoStart' -Confirm:$false
#
# Note: also enable "Start Docker Desktop when you log in" in Docker Desktop
# settings so the engine is up; the start script waits for it regardless.

$ErrorActionPreference = 'Stop'
$repo   = Split-Path -Parent $PSScriptRoot
$script = Join-Path $PSScriptRoot 'start-neuradex.ps1'
$task   = 'NeuradeX-AutoStart'

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`"" `
    -WorkingDirectory $repo

# At logon, delay so Docker Desktop + WSL2 have time to initialize.
# Cold boots need more time than restarts — 120 s is the safe minimum.
$trigger = New-ScheduledTaskTrigger -AtLogOn
$trigger.Delay = 'PT120S'

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0)

Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger `
    -Settings $settings -RunLevel Limited -Force `
    -Description 'Start NeuradeX and publish the live ngrok URL at logon' | Out-Null

Write-Host "Registered scheduled task '$task' - NeuradeX will start at logon." -ForegroundColor Green
Write-Host "Reminder: enable 'Start Docker Desktop when you log in' in Docker Desktop settings." -ForegroundColor Yellow
