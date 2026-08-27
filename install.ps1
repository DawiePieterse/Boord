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
# Gpg4win is what verifies signed releases in update_server.bat. Git for
# Windows bundles a gpg.exe, but it is unusable here: it keeps keys in a
# keyboxd daemon that the Git distribution does not ship, so importing a key
# fails with "probably not installed" and processes zero keys.
$Gpg4winUrl = "https://files.gpg4win.org/gpg4win-latest.exe"
$ReleaseKeyPath = Join-Path $RepoRoot "release-key.asc"
$FprFile = Join-Path $RepoRoot "data\release_key.fpr"

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

function Get-SmartAppControlState {
    # Windows 11's Smart App Control blocks executables whose publisher it
    # cannot verify - including installers downloaded by scripts like this
    # one. Worth knowing about before blaming ourselves for a failed install.
    #   0 = off   1 = on, enforcing   2 = evaluation mode   -1 = not present
    try {
        $v = Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy" `
                              -Name VerifiedAndReputablePolicyState -ErrorAction Stop
        return [int]$v.VerifiedAndReputablePolicyState
    } catch {
        return -1
    }
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

    # --- Step 2: Git (needed by update_server.bat, not by the server) ---
    Write-Step "Checking for Git..."
    $gitCmd = Get-Command git -ErrorAction SilentlyContinue
    if ($gitCmd) {
        Write-Ok "Found Git at $($gitCmd.Source)"
    } else {
        Write-Warn "Git is not installed. The server itself will still run, but"
        Write-Warn "update_server.bat cannot fetch releases without it."
        Write-Warn "Install it from https://git-scm.com/download/win and re-run this."
    }

    # --- Step 3: GnuPG, for verifying signed releases ---
    Write-Step "Checking for GnuPG (verifies signed releases)..."
    $gpgExe = $null
    $gpgCmd = Get-Command gpg -ErrorAction SilentlyContinue
    # Git's bundled gpg is deliberately rejected - see $Gpg4winUrl above. It is
    # on PATH inside Git Bash and would otherwise look like a working answer.
    if ($gpgCmd -and $gpgCmd.Source -notlike "*\Git\usr\bin\*") {
        $gpgExe = $gpgCmd.Source
        Write-Ok "Found GnuPG at $gpgExe"
    } else {
        Write-Warn "No usable GnuPG found - installing Gpg4win..."
        $sac = Get-SmartAppControlState

        function Find-InstalledGpg {
            foreach ($candidate in @(
                (Join-Path $env:ProgramFiles "GnuPG\bin\gpg.exe"),
                (Join-Path ${env:ProgramFiles(x86)} "GnuPG\bin\gpg.exe")
            )) {
                if (Test-Path $candidate) { return $candidate }
            }
            return $null
        }

        try {
            # winget first. Smart App Control blocks executables a script
            # downloaded itself, but it treats Microsoft's own package manager
            # differently - so on a machine with SAC enforcing this is the path
            # that actually works. It is also not a way around the policy:
            # winget checks the package the same way any other install does.
            $winget = Get-Command winget -ErrorAction SilentlyContinue
            if ($winget) {
                Write-Warn "Trying winget..."
                cmd /c "winget install --id GnuPG.Gpg4win --silent --accept-package-agreements --accept-source-agreements >nul 2>&1"
                $gpgExe = Find-InstalledGpg
            }

            if (-not $gpgExe) {
                if ($sac -eq 1) {
                    Write-Warn "Smart App Control is on - a downloaded installer may be blocked."
                }
                Write-Warn "Downloading Gpg4win directly..."
                $gpgInstaller = Join-Path $env:TEMP "gpg4win-latest.exe"
                Invoke-WebRequest -Uri $Gpg4winUrl -OutFile $gpgInstaller -UseBasicParsing
                Write-Warn "Installing Gpg4win (this can take a minute)..."
                Start-Process -FilePath $gpgInstaller -ArgumentList "/S" -Wait
                Remove-Item $gpgInstaller -ErrorAction SilentlyContinue
                $gpgExe = Find-InstalledGpg
            }
            if ($gpgExe) {
                Write-Ok "Installed GnuPG at $gpgExe"
            } elseif ($sac -eq 1) {
                # Do not report this as our failure. Nothing was installed,
                # and the reason is a Windows policy, not a broken download.
                Write-Warn "Windows Smart App Control blocked the Gpg4win installer."
                Write-Warn "Nothing was installed. Install it by hand from https://gpg4win.org"
                Write-Warn "and allow it when Windows asks, then re-run this installer."
                Write-Warn "Do NOT switch Smart App Control off to get around this - on"
                Write-Warn "Windows 11 it cannot be switched back on without resetting Windows."
            } else {
                Write-Warn "Gpg4win ran but gpg.exe was not found in the usual places."
                Write-Warn "Install it by hand from https://gpg4win.org and re-run this installer."
            }
        } catch {
            Write-Warn "Could not install Gpg4win automatically: $($_.Exception.Message)"
            Write-Warn "Install it by hand from https://gpg4win.org, then re-run this installer."
        }
    }

    # Everything below talks to native tools that report success on stderr.
    # With $ErrorActionPreference = "Stop", Windows PowerShell turns a native
    # command's redirected stderr into a TERMINATING error - the exact bug
    # that killed this installer at the schtasks step. gpg is worse than most:
    # "Total number processed: 1" goes to stderr even on a clean import. So
    # these run through cmd, which keeps their stderr away from PowerShell,
    # and the whole block is non-fatal - a farm can import the key by hand.
    if ($gpgExe -and $gitCmd) {
        try {
            $gpgForGit = $gpgExe -replace '\\', '/'
            cmd /c "git config --global gpg.program ""$gpgForGit"" >nul 2>&1"
            Write-Ok "Told git to use this gpg for signature checks"
        } catch {
            Write-Warn "Could not set git's gpg.program - set it by hand (MANUAL.md chapter 2)."
        }
    }

    if ($gpgExe -and (Test-Path $ReleaseKeyPath)) {
        # Importing the public key from the repo is safe: what actually decides
        # which releases are trusted is the fingerprint in data\release_key.fpr,
        # which lives outside the repo. A swapped key would not match it and
        # update_server.bat would refuse the release.
        #
        # Start-Process rather than a direct call or cmd /c. gpg reports even a
        # successful import on stderr, which PowerShell would turn into a
        # terminating error here; and cmd /c strips quotes from a command that
        # starts with one, which "C:\Program Files\..." does. Start-Process
        # sidesteps both and returns a real exit code.
        Write-Warn "Importing the release key (first gpg run can take a moment)..."
        try {
            $gpgLog = Join-Path $env:TEMP "boord-gpg-import.log"
            # Not -Wait. GnuPG 2.5 starts keyboxd and gpg-agent on its first
            # run, and on a machine whose keyring has just been created that
            # start-up can hang indefinitely with its output redirected into a
            # non-interactive process - which stalled the whole installer with
            # no message explaining what it was waiting for. Importing the key
            # is a convenience; it must never be able to block the install.
            $proc = Start-Process -FilePath $gpgExe `
                -ArgumentList @("--batch", "--yes", "--import", $ReleaseKeyPath) `
                -NoNewWindow -PassThru `
                -RedirectStandardError $gpgLog -RedirectStandardOutput "$gpgLog.out"
            # Touching .Handle forces .NET to keep the process handle open.
            # Without it, Start-Process -PassThru used without -Wait leaves
            # .ExitCode reading as $null once the process has gone, and
            # `$null -eq 0` is false - so a perfectly good import was reported
            # as "failed (exit )", with the exit code missing from the message
            # because there was never one to print.
            try { $null = $proc.Handle } catch { }
            if ($proc.WaitForExit(60000)) {
                $exit = $null
                try { $exit = $proc.ExitCode } catch { }
                if ($exit -eq 0) {
                    Write-Ok "Imported the Boord release key"
                } elseif ($null -eq $exit) {
                    # Report honestly rather than guessing either way.
                    Write-Warn "gpg finished but Windows did not report its exit code."
                    Write-Warn "The import probably worked. Confirm with:"
                    Write-Warn "    ""$gpgExe"" --list-keys"
                } else {
                    Write-Warn "Importing release-key.asc failed (exit $exit) - see $gpgLog"
                    Write-Warn "Import it by hand before running update_server.bat."
                }
            } else {
                try { $proc.Kill() } catch { }
                Write-Warn "gpg did not finish within 60 seconds - skipped the key import."
                Write-Warn "This is usually gpg's first run initialising its keyring. Run this"
                Write-Warn "once in a Command Prompt, which lets it finish interactively:"
                Write-Warn "    ""$gpgExe"" --import release-key.asc"
                Write-Warn "Setup will carry on without it."
            }
            Remove-Item "$gpgLog.out" -ErrorAction SilentlyContinue
        } catch {
            Write-Warn "Could not import release-key.asc: $($_.Exception.Message)"
            Write-Warn "Import it by hand before running update_server.bat."
        }
    }

    # --- Step 4: Create the virtual environment ---
    Write-Step "Setting up the app's virtual environment..."
    if (-not (Test-Path $VenvDir)) {
        & $pythonExe -m venv $VenvDir
        Write-Ok "Created virtual environment"
    } else {
        Write-Ok "Virtual environment already exists"
    }
    $venvPython = Join-Path $VenvDir "Scripts\python.exe"
    $venvPip = Join-Path $VenvDir "Scripts\pip.exe"

    # --- Step 5: Install dependencies ---
    Write-Step "Installing app dependencies (this can take a few minutes on first run)..."
    & $venvPip install --quiet --disable-pip-version-check -r (Join-Path $BackendDir "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Failed to install dependencies. Check the internet connection and re-run this installer."
        exit 1
    }
    Write-Ok "Dependencies installed"

    # --- Step 6: Write the launcher script ---
    Write-Step "Creating the server launcher..."
    $launcherPath = Join-Path $RepoRoot "start_server.bat"
    $launcherContent = @"
@echo off
cd /d "$BackendDir"
"$venvPython" -m uvicorn main:app --host 0.0.0.0 --port $Port
"@
    Set-Content -Path $launcherPath -Value $launcherContent -Encoding ASCII
    Write-Ok "Created $launcherPath"

    # --- Step 7: Firewall rule ---
    Write-Step "Allowing the app through Windows Firewall..."
    netsh advfirewall firewall delete rule name="$FirewallRuleName" | Out-Null
    netsh advfirewall firewall add rule name="$FirewallRuleName" dir=in action=allow protocol=TCP localport=$Port | Out-Null
    Write-Ok "Firewall rule set for port $Port"

    # --- Step 8: Scheduled task (auto-start at boot, no login needed) ---
    Write-Step "Registering the server to start automatically with Windows..."
    # Route schtasks through cmd so its stderr never reaches PowerShell.
    # $ErrorActionPreference = "Stop" (top of this file) turns a native
    # command's stderr into a TERMINATING error when it is redirected with
    # 2>&1 - and schtasks /query writes "ERROR: The system cannot find the
    # file specified." to stderr whenever the task is absent. That is the
    # normal state on a first-time install, so the installer completed every
    # other step and then died right here, on new machines only. On a
    # reinstall the task already existed, /query succeeded silently, and
    # nothing looked wrong - which is exactly why this survived so long.
    cmd /c "schtasks /query /tn ""$TaskName"" >nul 2>&1"
    if ($LASTEXITCODE -eq 0) {
        # /end likewise complains to stderr when the task exists but is not
        # running, so it gets the same treatment.
        cmd /c "schtasks /end /tn ""$TaskName"" >nul 2>&1"
        Start-Sleep -Seconds 1
        schtasks /delete /tn "$TaskName" /f | Out-Null
    }
    schtasks /create /tn "$TaskName" /tr "`"$launcherPath`"" /sc onstart /ru SYSTEM /rl highest /f | Out-Null
    Write-Ok "Scheduled task '$TaskName' registered (runs at every startup, no one needs to log in)"

    # --- Step 9: Start it now ---
    Write-Step "Starting the server now..."
    schtasks /run /tn "$TaskName" | Out-Null

    # --- Step 10: Confirm it actually answers ---
    # The installer used to sleep three seconds and declare success, so a
    # server that died on startup - a missing dependency, a port already in
    # use - still produced "Setup complete!". Poll until it responds instead,
    # and say plainly if it never does.
    $serverUp = $false
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Seconds 1
        try {
            $resp = Invoke-WebRequest -Uri "http://localhost:$Port/" -UseBasicParsing -TimeoutSec 3
            if ($resp.StatusCode -eq 200) { $serverUp = $true; break }
        } catch { }
    }
    if ($serverUp) {
        Write-Ok "Server is up and answering on port $Port"
    } else {
        Write-Warn "The server did not answer on port $Port within 20 seconds."
        Write-Warn "It may still be starting. If it never comes up, run start_server.bat"
        Write-Warn "directly in a window - errors are printed there rather than swallowed"
        Write-Warn "by the Scheduled Task."
    }

    # --- Step 11: Report the address ---
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

    # The one thing this installer cannot do for you. The fingerprint is what
    # decides which releases this server will accept, so it has to be typed in
    # by a person from a source they trust - not read from the repository,
    # which is the very thing it exists to check.
    if (-not (Test-Path $FprFile)) {
        Write-Host ""
        Write-Host "================================================" -ForegroundColor Yellow
        Write-Host " One step left: trust the release key" -ForegroundColor Yellow
        Write-Host "================================================" -ForegroundColor Yellow
        Write-Host " update_server.bat will refuse to install anything until this"
        Write-Host " server knows which signing key to trust. In this folder, run:"
        Write-Host ""
        Write-Host "     echo <FINGERPRINT>> data\release_key.fpr" -ForegroundColor Cyan
        Write-Host ""
        Write-Host " ...with the 40-character fingerprint from whoever maintains"
        Write-Host " this install. Note there is NO space before the > - echo would"
        Write-Host " write one into the file and the fingerprint would not match."
        Write-Host " See MANUAL.md chapter 2, 'Trusting the release key'."
    } else {
        $fpr = (Get-Content $FprFile -TotalCount 1).Trim()
        Write-Ok "Release key fingerprint on file: $fpr"
    }

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
    Write-Host " uninstall.bat       - unregisters the server from Windows and"
    Write-Host "                       removes the virtual environment. Never"
    Write-Host "                       touches data\ or this folder."
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
