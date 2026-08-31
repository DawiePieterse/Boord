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

$VersionUrl = "http://localhost:8000/api/version"

function Send-Ping($url, $body) {
    try {
        if ($body) {
            Invoke-WebRequest -Uri $url -Method Post -Body $body -ContentType "application/json" `
                -UseBasicParsing -TimeoutSec 10 | Out-Null
        } else {
            Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 10 | Out-Null
        }
    } catch { }
}

# What this farm is running, sent as the ping body so the version can be read
# from the monitoring account instead of phoning someone and asking them to
# read a screen. Best effort: if this fails the ping still goes out, just
# without a body. It is NOT part of the up/down decision - that stays the
# plain GET below, because this endpoint touches the database and a slow
# query must not turn a healthy farm into an alert.
#
# The payload is built field by field, on purpose. It would be shorter to
# forward the endpoint's JSON as-is, and then a field added to /api/version
# next year would start leaving the farm without anyone deciding it should.
#
# What must never go in here, whatever gets added to that endpoint:
#   - anything about a worker: names, ID numbers, banking, photos, counts
#   - supplier or block names, crate or lot figures - a competitor could read
#     a season's volume out of them
#   - GPS coordinates, hostnames, IP addresses, file paths, admin usernames
#   - the ping URL itself, which is a bearer credential
#   - git_error, which can contain a filesystem path
# The pack house name is deliberately absent too: this check already
# identifies the site by its own name in the monitoring account, so putting
# the customer's business name in a third party's log buys nothing.
#
# healthchecks.io is a third party. Anyone with access to that account can
# read these bodies. See MANUAL.md chapter 2, "Uptime alerting".
function Get-VersionBody {
    try {
        $raw = Invoke-WebRequest -Uri $VersionUrl -UseBasicParsing -TimeoutSec 10
        $info = $raw.Content | ConvertFrom-Json
    } catch {
        return $null
    }
    $payload = [ordered]@{
        tag              = $info.tag
        state            = $info.state
        frontend_version = $info.frontend_version
        alembic_head     = $info.alembic_head
        alembic_current  = $info.alembic_current
        last_backup      = $info.backups.last
        backup_count     = $info.backups.count
    }
    try { return ($payload | ConvertTo-Json -Compress) } catch { return $null }
}

try {
    $response = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 10
    if ($response.StatusCode -eq 200) {
        Send-Ping $PingUrl (Get-VersionBody)
    } else {
        Send-Ping "$PingUrl/fail"
    }
} catch {
    # Server didn't respond at all (crashed, hung, or PC/network down).
    # If there's no internet either, this fails silently too - in that
    # case the monitoring service's own silence-detection (grace period)
    # is the real backstop, since nothing on this PC can phone out at all.
    # No body: there is no version to read from a server that is not up.
    Send-Ping "$PingUrl/fail"
}
