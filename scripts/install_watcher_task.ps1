# ============================================================================
# SNM WORKS — INSTALL SPEC PDF WATCHER SCHEDULED TASK
# ============================================================================
# Registers the Spec PDF Watcher agent as a native Windows Scheduled Task
# running under the current interactive user account ($env:USERNAME).
#
# Requirements:
# - Runs automatically upon user logon
# - Restarts automatically upon failure (up to 5 attempts)
# - No hardcoded passwords or SYSTEM-level security risks
# ============================================================================

$ErrorActionPreference = "Stop"

$ProjectRoot = "c:\Users\ASUS\Downloads\SNM_WORKS"
$PythonExe = "C:\Users\ASUS\AppData\Local\Programs\Python\Python312\python.exe"
$ScriptPath = Join-Path $ProjectRoot "services\watcher.py"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "SNM Works — Spec PDF Ingestion Watcher Installer" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "User Account:    $env:USERNAME"
Write-Host "Project Root:    $ProjectRoot"
Write-Host "Python Path:     $PythonExe"
Write-Host "Watcher Script:  $ScriptPath"
Write-Host ""

if (-not (Test-Path $PythonExe)) {
    Write-Error "Python executable not found at: $PythonExe"
}

if (-not (Test-Path $ScriptPath)) {
    Write-Error "Watcher script not found at: $ScriptPath"
}

$TaskName = "SNMSpecWatcher"

# Check if task already exists
$ExistingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($ExistingTask) {
    Write-Host "Existing task '$TaskName' detected. Unregistering old version..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$Action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument $ScriptPath `
    -WorkingDirectory $ProjectRoot

$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

$Principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 0)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings

Write-Host ""
Write-Host "SUCCESS: Scheduled Task '$TaskName' registered successfully!" -ForegroundColor Green
Write-Host "The watcher will start automatically when you log in, or you can start it immediately with:" -ForegroundColor White
Write-Host "  Start-ScheduledTask -TaskName '$TaskName'" -ForegroundColor Yellow
Write-Host "To view watcher logs:" -ForegroundColor White
Write-Host "  Get-Content -Path '$ProjectRoot\logs\watcher.log' -Tail 50 -Wait" -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Cyan
