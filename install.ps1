# Boord - Windows server installer
#
# Automates MANUAL.md chapter 2's manual setup steps: installs Python if
# needed, creates the virtual environment, installs dependencies, writes
# the launcher script, opens the Windows Firewall port, and registers the
# server to auto-start at boot (as SYSTEM, so no login/password is needed
# for it to start). Safe to re-run - each step checks what's already done.
#
# Run via install.bat, which handles the administrator-elevation prompt.
# Running this file directly (not via install.bat) requires an already
#-elevated PowerShell window.

$ErrorActionPreference = "Stop"

$RepoRoot = $PSScriptRoot
$BackendDir = Join-Path $RepoRoot "backend"
$VenvDir = Join-Path $BackendDir ".venv"
$Port = 8000
$TaskName = "Boord Server"
$FirewallRuleName = "Boord Server"
$PythonVersion = "3.11.9"
$PythonInstallerUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-amd64.exe"

function Write-Step($msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}
function Write-Ok($msg) {
    Write-Host "    $msg" -ForegroundColor Green
}
function Write-Warn($msg) {
    Write-Host "    $msg" -ForegroundColor Yellow
}
function Write-Err($msg) {
    Write-Host "    $msg" -ForegroundColor Red
}

function Test-PythonOk($exe) {
    if (-not $exe -or -not (Test-Path $exe)) { return $false }
    try {
        $out = & $exe -c "import sys; print(sys.version_info[0]); print(sys.version_info[1]); print('64BIT' if sys.maxsize > 2**32 else '32BIT')" 2>$null
        if (-not $out -or $out.Count -lt 3) { return $false }
        $major = [int]$out[0]
        $minor = [int]$out[1]
        $bits = $out[2].Trim()
        return ($major -eq 3 -and $minor -ge 9 -and $bits -eq "64BIT")
    } catch {
        return $false
    }
}

try {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Err "This script needs to run as Administrator."
        Write-Err "Please run install.bat instead of this file directly - it handles that automatically."
        exit 1
    }

    Write-Host ""
    Write-Host "Boord - Server Installer" -ForegroundColor Cyan
    Write-Host "================================================" -ForegroundColor Cyan

    # --- Step 1: Find or install Python ---
    Write-Step "Checking for Python 3.9+ (64-bit)..."
    $pythonExe = $null

    $existing = Get-Command python -ErrorAction SilentlyContinue
    if ($existing -and (Test-PythonOk $existing.Source)) {
        $pythonExe = $existing.Source
        Write-Ok "Found a compatible Python at $pythonExe"
    } else {
        $wellKnownPath = Join-Path $env:ProgramFiles "Python311\python.exe"
        if (Test-PythonOk $wellKnownPath) {
            $pythonExe = $wellKnownPath
            Write-Ok "Found a compatible Python at $pythonExe"
        } else {
            Write-Warn "No compatible 64-bit Python 3.9+ found - downloading Python $PythonVersion..."
            $installerPath = Join-Path $env:TEMP "python-$PythonVersion-amd64.exe"
            Invoke-WebRequest -Uri $PythonInstallerUrl -OutFile $installerPath -UseBasicParsing
            Write-Warn "Installing Python (this can take a minute)..."
            Start-Process -FilePath $installerPath -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_test=0" -Wait
            Remove-Item $installerPath -ErrorAction SilentlyContinue
            $pythonExe = $wellKnownPath
            if (-not (Test-PythonOk $pythonExe)) {
                Write-Err "Python installation could not be confirmed at $pythonExe."
                Write-Err "Please install Python 3.9+ (64-bit) manually from python.org and re-run this installer."
                exit 1
            }
            Write-Ok "Installed Python at $pythonExe"
        }
    }

    # --- Step 2: Create the virtual environment ---
    Write-Step "Setting up the app's virtual environment..."
    if (-not (Test-Path $VenvDir)) {
        & $pythonExe -m venv $VenvDir
        Write-Ok "Created virtual environment"
    } else {
        Write-Ok "Virtual environment already exists"
    }
    $venvPython = Join-Path $VenvDir "Scripts\python.exe"
    $venvPip = Join-Path $VenvDir "Scripts\pip.exe"

    # --- Step 3: Install dependencies ---
    Write-Step "Installing app dependencies (this can take a few minutes on first run)..."
    & $venvPip install --quiet --disable-pip-version-check -r (Join-Path $BackendDir "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Failed to install dependencies. Check the internet connection and re-run this installer."
        exit 1
    }
    Write-Ok "Dependencies installed"

    # --- Step 4: Write the launcher script ---
    Write-Step "Creating the server launcher..."
    $launcherPath = Join-Path $RepoRoot "start_server.bat"
    $launcherContent = @"
@echo off
cd /d "$BackendDir"
"$venvPython" -m uvicorn main:app --host 0.0.0.0 --port $Port
"@
    Set-Content -Path $launcherPath -Value $launcherContent -Encoding ASCII
    Write-Ok "Created $launcherPath"

    # --- Step 5: Firewall rule ---
    Write-Step "Allowing the app through Windows Firewall..."
    netsh advfirewall firewall delete rule name="$FirewallRuleName" | Out-Null
    netsh advfirewall firewall add rule name="$FirewallRuleName" dir=in action=allow protocol=TCP localport=$Port | Out-Null
    Write-Ok "Firewall rule set for port $Port"

    # --- Step 6: Scheduled task (auto-start at boot, no login needed) ---
    Write-Step "Registering the server to start automatically with Windows..."
    schtasks /query /tn "$TaskName" >$null 2>&1
    if ($LASTEXITCODE -eq 0) {
        schtasks /end /tn "$TaskName" >$null 2>&1
        Start-Sleep -Seconds 1
        schtasks /delete /tn "$TaskName" /f | Out-Null
    }
    schtasks /create /tn "$TaskName" /tr "`"$launcherPath`"" /sc onstart /ru SYSTEM /rl highest /f | Out-Null
    Write-Ok "Scheduled task '$TaskName' registered (runs at every startup, no one needs to log in)"

    # --- Step 7: Start it now ---
    Write-Step "Starting the server now..."
    schtasks /run /tn "$TaskName" | Out-Null
    Start-Sleep -Seconds 3
    Write-Ok "Server starting in the background"

    # --- Step 8: Report the address ---
    Write-Step "Finding this PC's network address..."
    $ip = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" -and $_.PrefixOrigin -ne "WellKnown" } |
        Select-Object -First 1 -ExpandProperty IPAddress

    Write-Host ""
    Write-Host "================================================" -ForegroundColor Cyan
    Write-Host " Setup complete!" -ForegroundColor Green
    Write-Host "================================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host " On this PC:                        http://localhost:$Port/"
    if ($ip) {
        Write-Host " From other devices on the network: http://$ip`:$Port/"
    } else {
        Write-Warn "Could not detect this PC's network address automatically - run 'ipconfig' and look for 'IPv4 Address'."
    }
    Write-Host ""
    Write-Warn "Log in with username 'admin' and password 'ChangeMe123!', then"
    Write-Warn "change the password immediately under Settings - this installer"
    Write-Warn "does not do that step for you."
    Write-Host ""
    Write-Host " The server will now start automatically every time this PC turns on."

    Write-Host ""
    Write-Host "================================================" -ForegroundColor Cyan
    Write-Host " Optional extras available in this folder" -ForegroundColor Cyan
    Write-Host "================================================" -ForegroundColor Cyan
    Write-Host " These are separate, opt-in scripts - none of them ran just now."
    Write-Host " See MANUAL.md chapter 2 for details on each."
    Write-Host ""
    Write-Host " update_server.bat   - pulls the latest code from GitHub and"
    Write-Host "                       restarts the server in one step, for"
    Write-Host "                       future updates."
    Write-Host " setup_heartbeat.bat - emails you if the server goes down for"
    Write-Host "                       more than an hour (needs a free"
    Write-Host "                       healthchecks.io account first)."
} catch {
    Write-Host ""
    Write-Err "Something went wrong:"
    Write-Err $_.Exception.Message
    Write-Err "See MANUAL.md chapter 2 for the manual step-by-step setup as a fallback."
    exit 1
}
