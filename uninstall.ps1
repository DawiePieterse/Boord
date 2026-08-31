# Boord - Windows server uninstaller
#
# Removes what install.ps1 registered with Windows: the auto-start Scheduled
# Task, the firewall rule, the generated launcher, and the Python virtual
# environment.
#
# It deliberately does NOT touch data\. That folder holds the harvest
# database, worker photos and ID numbers, the wage history, the backups and
# the release-key fingerprint - a season of records that no amount of
# reinstalling brings back. An uninstaller that can destroy those with one
# double-click is a worse problem than no uninstaller at all, so removing
# data stays a deliberate act by a person who has read what they are about
# to delete. The script ends by printing exactly what is left and how to
# remove it.
#
# Run via uninstall.bat, which handles the administrator-elevation prompt.

$ErrorActionPreference = "Stop"

$RepoRoot = $PSScriptRoot
$BackendDir = Join-Path $RepoRoot "backend"
$VenvDir = Join-Path $BackendDir ".venv"
$DataDir = Join-Path $RepoRoot "data"
$LauncherPath = Join-Path $RepoRoot "start_server.bat"
$Port = 8000
$TaskName = "Boord Server"
$HeartbeatTaskName = "Boord Heartbeat"
$UpdateCheckTaskName = "Boord Update Check"
$FirewallRuleName = "Boord Server"

function Write-Step($msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}
function Write-Ok($msg)   { Write-Host "    $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "    $msg" -ForegroundColor Red }

function Get-FolderSize($path) {
    if (-not (Test-Path $path)) { return $null }
    try {
        $bytes = (Get-ChildItem $path -Recurse -File -ErrorAction SilentlyContinue |
                  Measure-Object -Property Length -Sum).Sum
        if (-not $bytes) { return "0 MB" }
        return "{0:N1} MB" -f ($bytes / 1MB)
    } catch { return "unknown size" }
}

try {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Err "This script needs to run as Administrator."
        Write-Err "Please run uninstall.bat instead of this file directly."
        exit 1
    }

    Write-Host ""
    Write-Host "Boord - Server Uninstaller" -ForegroundColor Cyan
    Write-Host "================================================" -ForegroundColor Cyan
    Write-Host " Removes the Windows service registration and the"
    Write-Host " virtual environment. Your data is NOT touched."

    # --- Step 1: Stop and remove the auto-start task ---
    # Every schtasks call goes through cmd. schtasks reports "cannot find the
    # file specified" on stderr when a task is absent, and with
    # $ErrorActionPreference = "Stop" PowerShell turns a native command's
    # redirected stderr into a terminating error - which is exactly how the
    # installer used to die on a first-time install.
    Write-Step "Stopping and removing the auto-start task..."
    cmd /c "schtasks /query /tn ""$TaskName"" >nul 2>&1"
    if ($LASTEXITCODE -eq 0) {
        cmd /c "schtasks /end /tn ""$TaskName"" >nul 2>&1"
        Start-Sleep -Seconds 2
        cmd /c "schtasks /delete /tn ""$TaskName"" /f >nul 2>&1"
        Write-Ok "Removed the '$TaskName' scheduled task"
    } else {
        Write-Ok "No '$TaskName' task registered - nothing to remove"
    }

    Write-Step "Removing the heartbeat task (if it was ever set up)..."
    cmd /c "schtasks /query /tn ""$HeartbeatTaskName"" >nul 2>&1"
    if ($LASTEXITCODE -eq 0) {
        cmd /c "schtasks /delete /tn ""$HeartbeatTaskName"" /f >nul 2>&1"
        Write-Ok "Removed the '$HeartbeatTaskName' scheduled task"
    } else {
        Write-Ok "No heartbeat task registered"
    }

    Write-Step "Removing the update-check task (if it was ever set up)..."
    cmd /c "schtasks /query /tn ""$UpdateCheckTaskName"" >nul 2>&1"
    if ($LASTEXITCODE -eq 0) {
        cmd /c "schtasks /delete /tn ""$UpdateCheckTaskName"" /f >nul 2>&1"
        Write-Ok "Removed the '$UpdateCheckTaskName' scheduled task"
    } else {
        Write-Ok "No update-check task registered"
    }

    # --- Step 2: Make sure the server is really stopped ---
    # schtasks /end kills the launcher, but the uvicorn process it spawned can
    # outlive it and keep holding backend\.venv - which then blocks the venv
    # removal below and, later, any attempt to delete the folder.
    #
    # This used to be done inline here. It now lives in stop_server.ps1, which
    # update_server.bat also calls before migrating - one implementation, so
    # the two paths cannot drift apart about what "stopped" means. Note the
    # task has already been deleted above, so this call only does the port
    # check and the cleanup; that is the part that matters here.
    $stopScript = Join-Path $RepoRoot "stop_server.ps1"
    if (Test-Path $stopScript) {
        & $stopScript -Port $Port -TaskName $TaskName
        if ($LASTEXITCODE -ne 0) {
            Write-Warn "Something is still holding port $Port - stop it by hand before deleting the folder."
        }
    } else {
        Write-Warn "stop_server.ps1 is missing - skipping the port check."
    }

    # --- Step 3: Firewall rule ---
    Write-Step "Removing the firewall rule..."
    cmd /c "netsh advfirewall firewall delete rule name=""$FirewallRuleName"" >nul 2>&1"
    Write-Ok "Firewall rule '$FirewallRuleName' removed (if it existed)"

    # --- Step 4: Generated launcher ---
    Write-Step "Removing the generated launcher..."
    if (Test-Path $LauncherPath) {
        Remove-Item $LauncherPath -Force
        Write-Ok "Deleted start_server.bat"
    } else {
        Write-Ok "No start_server.bat to remove"
    }

    # --- Step 5: Virtual environment ---
    Write-Step "Removing the Python virtual environment..."
    if (Test-Path $VenvDir) {
        $venvSize = Get-FolderSize $VenvDir
        try {
            Remove-Item $VenvDir -Recurse -Force
            Write-Ok "Deleted backend\.venv ($venvSize reclaimed)"
        } catch {
            Write-Warn "Could not delete backend\.venv - something still has a file open there."
            Write-Warn "Reboot and re-run this, or delete the folder by hand."
        }
    } else {
        Write-Ok "No virtual environment to remove"
    }

    # --- Step 6: Say plainly what is left ---
    Write-Host ""
    Write-Host "================================================" -ForegroundColor Green
    Write-Host " Boord is no longer registered with Windows" -ForegroundColor Green
    Write-Host "================================================" -ForegroundColor Green
    Write-Host " The server will not start at boot any more, and the"
    Write-Host " firewall no longer allows port $Port."

    Write-Host ""
    Write-Host " Deliberately left in place:" -ForegroundColor Yellow

    if (Test-Path $DataDir) {
        Write-Host ""
        Write-Host "   $DataDir  ($(Get-FolderSize $DataDir))" -ForegroundColor Yellow
        Write-Host "   The harvest database, worker photos and ID numbers, wage"
        Write-Host "   history, backups, and the release-key fingerprint."
        Write-Host "   Copy this somewhere safe before deleting anything."
    }

    Write-Host ""
    Write-Host "   $RepoRoot" -ForegroundColor Yellow
    Write-Host "   The app's code. Harmless to leave; it does nothing on its own."

    Write-Host ""
    Write-Host "   Python, Git and Gpg4win are untouched - other things may use them."
    Write-Host "   So is git's gpg.program setting and your GnuPG keyring."

    Write-Host ""
    Write-Host " To finish removing Boord entirely:" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "   1. Copy your data somewhere off this PC first:"
    Write-Host "        xcopy /E /I ""$DataDir"" ""%USERPROFILE%\Desktop\boord-data-backup"""
    Write-Host ""
    Write-Host "   2. Then, from a folder OUTSIDE this one:"
    Write-Host "        rmdir /s /q ""$RepoRoot"""
    Write-Host ""
    Write-Host "   That second command is irreversible and takes the database,"
    Write-Host "   the photos and all backups with it."
} catch {
    Write-Host ""
    Write-Err "Something went wrong:"
    Write-Err $_.Exception.Message
    Write-Err "Nothing further was removed. See MANUAL.md chapter 2."
    exit 1
}
