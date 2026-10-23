# Boord - the watchdog has moved to Boord Monitor.
#
# Until Boord Monitor 2.0 this file was the watchdog: every 5 minutes it
# checked every app on the PC and restarted any that had stopped answering.
# It now lives in Boord Monitor's folder (watchdog.ps1 there), beside the one
# table of the farm's apps (apps.json) that the monitor reads too - three
# hand-kept copies of that table became one.
#
# This file stays only so nothing breaks on the way across. A "Boord
# Watchdog" task registered before the move still runs this file; it hands
# every argument (-CheckOnly, -Restart "<Name>", ...) to the monitor's copy
# and passes its exit code back, so a restart asked for by an older monitor
# still works. Running Boord Monitor's install.bat once re-points the task
# at the monitor's folder, after which nothing runs this file at all.
#
# Without Boord Monitor 2.0 on this PC there is nothing to hand over to, and
# that is written to data\watchdog.log as a FAILED line - which the
# monitor's Watchdog log card shows amber - rather than failing silently.

$ErrorActionPreference = "Stop"
$LogFile = Join-Path (Join-Path $PSScriptRoot "data") "watchdog.log"

function Write-Log($msg) {
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Write-Host $line
    try {
        $dir = Split-Path -Parent $LogFile
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        Add-Content -Path $LogFile -Value $line -Encoding ASCII
    } catch { }
}

# Boord Monitor's folder, read from its scheduled task the way the watchdog
# has always found every app's folder: the task's action is the full path of
# its start_monitor_server.bat, at the top of its repo.
function Get-MonitorRoot {
    try {
        $raw = cmd /c "schtasks /query /tn ""Boord Monitor Server"" /xml ONE 2>nul"
        if ($LASTEXITCODE -ne 0 -or -not $raw) { return $null }
        $xml = [xml](($raw -join "`n").TrimStart([char]0xFEFF))
        $cmdPath = "$($xml.Task.Actions.Exec.Command)".Trim('"')
        if (-not $cmdPath -or -not (Test-Path $cmdPath)) { return $null }
        return (Split-Path -Parent $cmdPath)
    } catch {
        return $null
    }
}

$root = Get-MonitorRoot
$target = if ($root) { Join-Path $root "watchdog.ps1" } else { $null }
if (-not $target -or -not (Test-Path $target)) {
    Write-Log ("FAILED: the watchdog now runs from Boord Monitor 2.0 or later, and it is not on this PC" +
        $(if ($root) { " ($root has no watchdog.ps1 - update Boord Monitor)" } else { "" }) +
        ". Nothing was checked or restarted. Install or update Boord Monitor, then run its install.bat.")
    exit 1
}

& powershell -NoProfile -ExecutionPolicy Bypass -File $target @args
exit $LASTEXITCODE
