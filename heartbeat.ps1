# Boord - the heartbeat has moved to Boord Monitor.
#
# Until Boord Monitor 2.0 this file pinged healthchecks.io for each app on
# the PC, so someone is emailed when the PC itself is off. It now lives in
# Boord Monitor's folder (heartbeat.ps1 there), reading the same app table
# as the monitor and the watchdog, and its ping URLs moved from this
# folder's heartbeat_url.txt to the monitor's data\heartbeat_url.txt.
#
# This file stays only so nothing breaks on the way across. A "Boord
# Heartbeat" task registered before the move still runs this file; it copies
# heartbeat_url.txt across if the monitor has none yet, then hands over to
# the monitor's copy. Running Boord Monitor's install.bat once does the same
# copy and re-points the task, after which nothing runs this file at all.
#
# Without Boord Monitor 2.0 on this PC there is nothing to hand over to: no
# pings go out, and healthchecks.io emails once its grace period runs out -
# which is the heartbeat working as designed, if late.

$ErrorActionPreference = "Stop"

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
$target = if ($root) { Join-Path $root "heartbeat.ps1" } else { $null }
if (-not $target -or -not (Test-Path $target)) {
    Write-Host "The heartbeat now runs from Boord Monitor 2.0 or later, and it is not on this PC."
    Write-Host "Install or update Boord Monitor, then run its install.bat."
    exit 1
}

# The ping URLs are a credential: copied, never moved, and never over an
# existing file - the monitor's copy wins once it has one.
$oldUrls = Join-Path $PSScriptRoot "heartbeat_url.txt"
$newUrls = Join-Path (Join-Path $root "data") "heartbeat_url.txt"
if ((Test-Path $oldUrls) -and -not (Test-Path $newUrls)) {
    try {
        New-Item -ItemType Directory -Path (Split-Path -Parent $newUrls) -Force | Out-Null
        Copy-Item -Path $oldUrls -Destination $newUrls
    } catch { }
}

& powershell -NoProfile -ExecutionPolicy Bypass -File $target @args
exit $LASTEXITCODE
