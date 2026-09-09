# Boord - server heartbeat for uptime alerting.
#
# Run periodically via a Scheduled Task (set up by setup_heartbeat.bat).
# Only pings the monitoring service (healthchecks.io) when an app itself
# actually responds on localhost, so a crashed/hung server - not just a
# powered-off PC - also gets caught, not just "is the PC on".
#
# This is the half of uptime that watchdog.ps1 cannot do. The watchdog runs
# on this PC, so it can restart a dead server but can tell you nothing when
# the PC is off, asleep or off the internet. This one cannot fix anything,
# but it is the only one of the two that reaches a person when the PC itself
# is the problem. Register both.
#
# Ping URLs are account-specific and, like passwords, are kept out of git
# entirely - they live in heartbeat_url.txt next to this script (gitignored).
# See MANUAL.md chapter 2, "Uptime alerting", for the format and for how to
# create the checks.

param(
    # Report what would be pinged and ping nothing. setup_heartbeat.bat runs
    # this after registering the task, because a mistyped key in
    # heartbeat_url.txt otherwise means no alerting at all for that app - and
    # the whole failure mode this file exists to prevent is silence that looks
    # exactly like health.
    [switch]$DryRun
)

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$urlFile = Join-Path $here "heartbeat_url.txt"

# The four servers that can share this PC. Aliases are what may appear on the
# left of the "=" in heartbeat_url.txt; they are matched with spaces, hyphens
# and underscores stripped and case ignored, so "Boord Owner", "boord-owner"
# and "owner" are all the same key.
$Apps = @(
    @{ Name = "Boord";       Port = 8000; Aliases = @("boord") },
    @{ Name = "Boord Owner"; Port = 8010; Aliases = @("owner", "boordowner") },
    @{ Name = "Boord Notes"; Port = 8020; Aliases = @("notes", "boordnotes") },
    @{ Name = "Kudde";       Port = 8030; Aliases = @("kudde") }
)

if (-not (Test-Path $urlFile)) {
    Write-Host "heartbeat_url.txt not found next to heartbeat.ps1 - see MANUAL.md chapter 2, 'Uptime alerting'."
    exit 1
}

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
# without a body - which is also what happens for the apps that have no
# /api/version endpoint at all. It is NOT part of the up/down decision - that
# stays the plain GET below, because this endpoint touches the database and a
# slow query must not turn a healthy farm into an alert.
#
# The payload is built field by field, on purpose. It would be shorter to
# forward the endpoint's JSON as-is, and then a field added to /api/version
# next year would start leaving the farm without anyone deciding it should.
# That matters more now than it did: this runs against four different
# applications, and only two of them are ones whose version endpoint was
# reviewed for what it exposes.
#
# What must never go in here, whatever gets added to any of those endpoints:
#   - anything about a worker: names, ID numbers, banking, photos, counts
#   - supplier or block names, crate or lot figures - a competitor could read
#     a season's volume out of them
#   - anything about an animal, a herd or a camp, for the same reason
#   - GPS coordinates, hostnames, IP addresses, file paths, admin usernames
#   - the ping URL itself, which is a bearer credential
#   - git_error, which can contain a filesystem path
# The pack house name is deliberately absent too: this check already
# identifies the site by its own name in the monitoring account, so putting
# the customer's business name in a third party's log buys nothing.
#
# healthchecks.io is a third party. Anyone with access to that account can
# read these bodies. See MANUAL.md chapter 2, "Uptime alerting".
function Get-VersionBody($port) {
    try {
        $raw = Invoke-WebRequest -Uri "http://127.0.0.1:$port/api/version" -UseBasicParsing -TimeoutSec 10
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

# --- Read the ping URLs ------------------------------------------------------
# Two accepted shapes, so a farm set up before this file could name more than
# one application keeps working untouched:
#
#   https://hc-ping.com/aaa          a bare URL on its own line is Boord's
#   owner = https://hc-ping.com/bbb  everything else is named
#
# Blank lines and lines starting with # are ignored.
$urls = @{}
$bare = @()
$problems = @()
foreach ($line in (Get-Content $urlFile)) {
    $t = "$line".Trim()
    if (-not $t -or $t.StartsWith("#")) { continue }
    if ($t -match '^\s*([A-Za-z][A-Za-z0-9 _-]*?)\s*=\s*(\S+)\s*$') {
        $key = ($matches[1].ToLower() -replace '[\s_-]', '')
        $app = $Apps | Where-Object { $_.Aliases -contains $key } | Select-Object -First 1
        if ($app) {
            $urls[$app.Name] = $matches[2]
        } else {
            $problems += "'$($matches[1].Trim())' is not an application this knows about, so that line does nothing. Use one of: boord, owner, notes, kudde."
        }
    } elseif ($t -match '^https?://') {
        $bare += $t
    } else {
        $problems += "Could not read this line, so it does nothing: $t"
    }
}

# A bare URL means Boord - that is what every install had before this file
# could name more than one application, and those files must keep working
# untouched. But it only fills a slot no explicit "boord =" line has taken,
# and a second bare URL is refused rather than quietly overwriting the first.
# Pasting a new check's URL on its own line is the obvious way to try to add
# an application, and the cost of getting that wrong is silent: Boord's
# alerting would move to the new check and the app you meant to watch would
# never be reported on at all, with nothing on screen to say so.
if ($bare.Count -gt 0) {
    if ($urls.ContainsKey("Boord")) {
        $problems += "There is a URL on a line of its own as well as a 'boord =' line. A bare URL means Boord, so the bare one is ignored - give it an application name, e.g. 'kudde = <url>'."
    } else {
        $urls["Boord"] = $bare[0]
    }
    if ($bare.Count -gt 1) {
        $problems += "$($bare.Count) URLs are on lines of their own, but only one line may be bare (it means Boord). Name the others, e.g. 'owner = <url>'."
    }
}

if ($DryRun) {
    Write-Host ""
    Write-Host "heartbeat_url.txt -> what this PC will report on:"
    Write-Host ""
    foreach ($app in $Apps) {
        if ($urls.ContainsKey($app.Name)) {
            $u = $urls[$app.Name]
            # Never print a ping URL in full: it is a bearer credential, and
            # this output goes on whatever screen somebody is sharing.
            $shown = if ($u.Length -gt 28) { $u.Substring(0, 28) + "..." } else { $u }
            Write-Host ("  {0,-12} port {1}  -> {2}" -f $app.Name, $app.Port, $shown)
        } else {
            Write-Host ("  {0,-12} port {1}  -> no check configured, will not be reported on" -f $app.Name, $app.Port)
        }
    }
    foreach ($msg in $problems) {
        Write-Host ""
        Write-Host "  WARNING: $msg" -ForegroundColor Yellow
    }
    Write-Host ""
    exit 0
}

if ($urls.Count -eq 0) {
    Write-Host "heartbeat_url.txt has no usable ping URL in it - see MANUAL.md chapter 2, 'Uptime alerting'."
    exit 1
}

# --- Report on each configured app -------------------------------------------
# An app with a URL is always reported on, whether or not it is installed
# here. Staying silent about a check somebody deliberately configured would
# let the monitoring service's grace period expire and email them anyway, but
# hours later and with no clue why - so a configured check for an app that is
# not on this PC correctly reads as "not answering".
foreach ($app in $Apps) {
    if (-not $urls.ContainsKey($app.Name)) { continue }
    $pingUrl = $urls[$app.Name]
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$($app.Port)/" -UseBasicParsing -TimeoutSec 10
        if ($response.StatusCode -eq 200) {
            Send-Ping $pingUrl (Get-VersionBody $app.Port)
        } else {
            Send-Ping "$pingUrl/fail"
        }
    } catch {
        # Didn't respond at all (crashed, hung, or PC/network down). If there
        # is no internet either, this fails silently too - in that case the
        # monitoring service's own silence-detection (grace period) is the
        # real backstop, since nothing on this PC can phone out at all.
        # No body: there is no version to read from a server that is not up.
        Send-Ping "$pingUrl/fail"
    }
}
