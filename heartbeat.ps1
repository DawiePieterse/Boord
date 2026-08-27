# Boord - server heartbeat for uptime alerting.
#
# Run periodically via a Scheduled Task (set up by setup_heartbeat.bat).
# Only pings the monitoring service (healthchecks.io) when the app itself
# actually responds on localhost, so a crashed/hung server - not just a
# powered-off PC - also gets caught, not just "is the PC on".
#
# The ping URL is account-specific and, like a password, is kept out of
# git entirely - it lives in heartbeat_url.txt next to this script
# (gitignored), one line, nothing else. See MANUAL.md chapter 2,
# "Uptime alerting", for how to create that file.

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$urlFile = Join-Path $here "heartbeat_url.txt"
$HealthUrl = "http://localhost:8000/"

if (-not (Test-Path $urlFile)) {
    Write-Host "heartbeat_url.txt not found next to heartbeat.ps1 - see MANUAL.md chapter 2, 'Uptime alerting'."
    exit 1
}
$PingUrl = (Get-Content $urlFile -Raw).Trim()

function Send-Ping($url) {
    try { Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 10 | Out-Null } catch { }
}

try {
    $response = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 10
    if ($response.StatusCode -eq 200) {
        Send-Ping $PingUrl
    } else {
        Send-Ping "$PingUrl/fail"
    }
} catch {
    # Server didn't respond at all (crashed, hung, or PC/network down).
    # If there's no internet either, this fails silently too - in that
    # case the monitoring service's own silence-detection (grace period)
    # is the real backstop, since nothing on this PC can phone out at all.
    Send-Ping "$PingUrl/fail"
}
