@echo off
setlocal EnableDelayedExpansion
:: Boord - double-click this file to update to the newest signed release
:: from GitHub, install any new dependencies, and restart the server so the
:: update actually takes effect.
:: See MANUAL.md chapter 2 ("Pulling future updates") for what this automates
:: and how to do it by hand.
::
:: This deliberately does NOT "git pull" a branch. A branch pull trusts
:: whoever can push to the repo, and this script runs elevated on a machine
:: whose server runs as SYSTEM - so a stolen GitHub token would mean code
:: execution on every farm running Boord. Instead it checks out a tag that
:: carries a GPG signature from the release key, and refuses to update at all
:: if that signature is missing, broken, or made by any other key. Pushing
:: code is then not enough to ship it; you also have to hold the signing key.

:: --check looks for a newer signed release and writes what it found to
:: data\update_available.json, without checking anything out. It is what the
:: "Boord Update Check" scheduled task runs, and it shares this script rather
:: than living in one of its own on purpose: the fingerprint pinning, the tag
:: selection and the signature check below are the security-critical part, and
:: a second copy of them would be a second thing to keep right.
set "CHECK_ONLY="
if /i "%~1"=="--check" set "CHECK_ONLY=1"
if /i "%~1"=="/check" set "CHECK_ONLY=1"

:: Only the update itself needs administrator rights - it restarts the server.
:: Checking needs none, and asking for them would break it: run unattended
:: from a Scheduled Task there is no desktop to show a UAC prompt on, so
:: Start-Process -Verb RunAs either fails silently or hangs forever.
:: Jumped over rather than wrapped in a block: with delayed expansion on,
:: %errorLevel% inside a parenthesised block is expanded when the block is
:: parsed - before net session has run - so the check would silently always
:: see the previous command's code.
if defined CHECK_ONLY goto :skip_elevation
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo This needs administrator rights to restart the server - requesting them now...
    echo If Windows shows a User Account Control prompt, click "Yes".
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)
:skip_elevation

cd /d "%~dp0"

:: The fingerprint of the key that is allowed to sign releases for THIS
:: server. It lives in data\ rather than in the checkout on purpose: a file
:: inside the repo would be rewritten by the very update it is supposed to be
:: vouching for, so an attacker who could push could also swap the
:: fingerprint for their own. data\ is gitignored, so a checkout never
:: touches it - it is set once during install and only changes if you
:: deliberately rotate the release key.
set "FPR_FILE=%~dp0data\release_key.fpr"
set "RELEASE_FPR="

echo.
echo ==^> Checking this server can verify signed releases...
if not exist "%FPR_FILE%" (
    echo.
    echo No release key fingerprint found at:
    echo     %FPR_FILE%
    echo.
    echo This server cannot tell a genuine Boord release from a tampered
    echo one, so it will not update. See MANUAL.md chapter 2, "Trusting the
    echo release key", for the one-time setup - it is two commands.
    echo.
    echo The server has NOT been restarted and is still running whatever it
    echo was running before.
    if not defined CHECK_ONLY pause
    exit /b 1
)
for /f "usebackq eol=# tokens=1 delims= " %%F in ("%FPR_FILE%") do (
    if not defined RELEASE_FPR set "RELEASE_FPR=%%F"
)
if not defined RELEASE_FPR (
    echo %FPR_FILE% is empty - expected a 40-character key fingerprint.
    echo Not updating. See MANUAL.md chapter 2, "Trusting the release key".
    if not defined CHECK_ONLY pause
    exit /b 1
)
echo     Only releases signed by !RELEASE_FPR! will be accepted.

:: Probe that git can actually run gpg before relying on it. A missing or
:: broken GnuPG makes verify-tag fail exactly like a bad signature does, so
:: without this check a farm with no gpg installed is told its release looks
:: tampered with - which sends them looking for an attacker instead of an
:: installer. Git for Windows' own bundled gpg fails this way too: it stores
:: keys in a keyboxd daemon the Git distribution does not ship.
set "GPG_PROG="
for /f "delims=" %%G in ('git config --get gpg.program 2^>nul') do set "GPG_PROG=%%G"
if not defined GPG_PROG set "GPG_PROG=gpg"
"%GPG_PROG%" --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo GnuPG is not working on this server, so signed releases cannot be
    echo checked. Nothing has been updated.
    echo.
    echo Tried: !GPG_PROG!
    echo.
    echo Install Gpg4win from https://gpg4win.org, then point git at it:
    echo     git config --global gpg.program "C:/Program Files/GnuPG/bin/gpg.exe"
    echo.
    echo Re-running install.bat does both of these for you.
    if not defined CHECK_ONLY pause
    exit /b 1
)

echo.
echo ==^> Fetching signed releases from GitHub...
:: --force so a retagged release is picked up rather than silently keeping
:: the stale local tag. It still has to pass the signature check below.
git fetch --tags --force origin
if %errorLevel% neq 0 (
    echo.
    echo Fetch failed - check the error above ^(no internet, or this folder
    echo isn't a git checkout^). The server has NOT been restarted, so it's
    echo still running whatever it was before.
    if not defined CHECK_ONLY pause
    exit /b 1
)

:: Newest release tag by version order, not by date - a tag's date is
:: attacker-controlled, its version number is what humans reason about.
set "NEWTAG="
for /f "delims=" %%T in ('git tag --list "v*" --sort^=-v:refname 2^>nul') do (
    if not defined NEWTAG set "NEWTAG=%%T"
)
if not defined NEWTAG (
    echo.
    echo No release tags ^(v*^) found in this repository. Nothing to update to.
    echo The server has NOT been restarted.
    if not defined CHECK_ONLY pause
    exit /b 1
)

set "CURTAG=not on a release tag"
for /f "delims=" %%C in ('git describe --tags --exact-match --match "v*" HEAD 2^>nul') do set "CURTAG=%%C"

echo.
echo     Currently running: !CURTAG!
echo     Newest release:    !NEWTAG!

if "!CURTAG!"=="!NEWTAG!" (
    echo     Already on the newest signed release - skipping the code update.
    if defined CHECK_ONLY (
        call :write_check_result ok false
        exit /b 0
    )
) else (
    echo.
    echo ==^> Verifying the signature on !NEWTAG!...
    :: --raw prints GPG's machine-readable status lines. A VALIDSIG line means
    :: the signature is good; requiring OUR fingerprint on that line is the
    :: part that matters, because a plain "good signature" only proves the tag
    :: was signed by *some* key present in this machine's keyring.
    :: Written to a file rather than piped through findstr twice. A pipe makes
    :: cmd spawn a child shell per stage, and worse, it throws away the output
    :: needed to tell WHY a check failed - so a missing key and a tampered
    :: repository produced the same alarming message.
    set "VERIFY_OUT=%TEMP%\boord_verify.txt"
    git verify-tag --raw "!NEWTAG!" > "!VERIFY_OUT!" 2>&1
    findstr /C:"VALIDSIG" "!VERIFY_OUT!" > "!VERIFY_OUT!.sig"
    findstr /I /C:"!RELEASE_FPR!" "!VERIFY_OUT!.sig" >nul
    if errorlevel 1 (
        findstr /C:"NO_PUBKEY" "!VERIFY_OUT!" >nul
        if not errorlevel 1 (
            echo.
            echo The release key is not in this server's keyring, so !NEWTAG!
            echo cannot be checked. This is NOT a sign of tampering - the key
            echo simply has not been imported on this machine yet.
            echo.
            echo Import it and run this again:
            echo     "!GPG_PROG!" --import release-key.asc
            echo.
            echo Nothing has been changed. The server is still running !CURTAG!.
            if defined CHECK_ONLY call :write_check_result no-pubkey false
        ) else (
            echo.
            echo *** SIGNATURE CHECK FAILED for !NEWTAG! ***
            echo.
            echo This release is not signed by the key this server trusts. That
            echo means one of:
            echo   - the release key was rotated and this server wasn't told
            echo   - the release genuinely wasn't signed
            echo   - someone tampered with the repository
            echo.
            echo Full gpg output: !VERIFY_OUT!
            echo.
            echo Nothing has been changed. The server has NOT been restarted and
            echo is still running !CURTAG!. Do not work around this by checking
            echo the tag out by hand - find out why it failed first.
            if defined CHECK_ONLY call :write_check_result failed false
        )
        if not defined CHECK_ONLY pause
        exit /b 1
    )
    echo     Signature OK.

    if defined CHECK_ONLY (
        echo     !NEWTAG! is available and is properly signed.
        call :write_check_result ok true
        exit /b 0
    )

    echo.
    echo ==^> Updating to !NEWTAG!...
    :: --force so a half-finished edit on the server can't block a deploy.
    :: Anything under data\ is gitignored and is left alone; only tracked
    :: code files are reset to exactly what the signed tag contains.
    git checkout --force "!NEWTAG!"
    if errorlevel 1 (
        echo.
        echo Checkout failed - check the error above. The server has NOT been
        echo restarted, so it's still running whatever it was before.
        if not defined CHECK_ONLY pause
        exit /b 1
    )
)

:: Leave a note of the tag that is now checked out, for the version endpoint
:: to fall back on when git cannot answer - a copy taken without .git, or a
:: repository git refuses to read because it is owned by another user. Written
:: on both branches above, including the "already newest" one, so a server
:: that never needed an update still ends up with a correct note.
:: No space before the > - echo would write one into the file.
echo !NEWTAG!> "%~dp0data\installed_version.txt"

echo.
echo ==^> Installing any new dependencies...
call "%~dp0backend\.venv\Scripts\activate.bat"
pip install --quiet --disable-pip-version-check -r "%~dp0backend\requirements.txt"
if %errorLevel% neq 0 (
    echo Warning: dependency install reported a problem - see the error above.
    echo Checking whether the server can still start before going any further...
)

:: This used to just warn and carry on, on the reasoning that most updates
:: don't change requirements.txt. That stopped being safe the moment schema
:: migrations became a dependency: a release that adds one cannot run at all
:: without it, so a half-finished pip install would take the farm from "an
:: update didn't apply" to "the server no longer starts", discovered by
:: whoever opens the Field app next morning. Ask the venv directly.
"%~dp0backend\.venv\Scripts\python.exe" -c "import fastapi, sqlmodel, alembic" >nul 2>&1
if errorlevel 1 (
    echo.
    echo The server's dependencies are not fully installed, so this update
    echo cannot be applied. Nothing has been migrated and the server has NOT
    echo been restarted - it is still running whatever it was before.
    echo.
    echo This is nearly always no internet, or pip being blocked. Try again
    echo once the machine is online:
    echo     update_server.bat
    echo.
    if not defined CHECK_ONLY pause
    exit /b 1
)

echo.
echo ==^> Restarting the server...
schtasks /query /tn "Boord Server" >nul 2>&1
if %errorLevel% equ 0 (
    echo.
    echo ==^> Stopping the server...
    :: Not just "schtasks /end". That ends the launcher; the uvicorn process
    :: it spawned can outlive it and carry on serving requests with the
    :: database open - which is the one thing that must not be true while
    :: migrations run. stop_server.ps1 ends the task and then checks port
    :: 8000 is actually free, and refuses rather than guessing.
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_server.ps1"
    if errorlevel 1 (
        echo.
        echo *** THE SERVER COULD NOT BE STOPPED ***
        echo.
        echo Something is still listening on port 8000, so the database is
        echo still open. Migrating now would rewrite tables underneath a
        echo running server, and it would not even report an error.
        echo.
        echo Nothing has been changed. The code is on !NEWTAG! but the server
        echo was never restarted, so it is still serving !CURTAG!. Read the
        echo message above for what holds the port, stop it, and run this
        echo again.
        echo.
        if not defined CHECK_ONLY pause
        exit /b 1
    )

    echo.
    echo ==^> Bringing the database up to date...
    echo     The server would do this by itself on startup. It is run here,
    echo     with the server stopped, so that a schema change happens in
    echo     front of the person who chose to update rather than inside a
    echo     Scheduled Task nobody is watching. A copy of the database is
    echo     taken first, into data\backups\, and nothing is altered unless
    echo     that copy was written.
    "%~dp0backend\.venv\Scripts\python.exe" "%~dp0backend\migrate.py"
    if errorlevel 1 (
        echo.
        echo *** THE DATABASE COULD NOT BE MIGRATED ***
        echo.
        echo The server has been left stopped on purpose - starting it now
        echo would only fail the same way, against a database this release
        echo does not understand.
        echo.
        echo Read the error above. If you need the farm working again before
        echo it can be sorted out, go back to the release that was running
        echo and restart:
        echo     git checkout --force !CURTAG!
        echo     schtasks /run /tn "Boord Server"
        echo.
        echo The database itself was copied before anything touched it - look
        echo for the newest pre_migration_*.db in data\backups\.
        echo.
        if not defined CHECK_ONLY pause
        exit /b 1
    )

    schtasks /run /tn "Boord Server" >nul 2>&1
    echo Server restarted.
) else (
    echo Could not find the "Boord Server" scheduled task - if
    echo you're running the server manually in its own window instead,
    echo close and reopen that window yourself to pick up the update.
)

echo.
echo ==^> Done - now running !NEWTAG!. Remember: each phone still needs its
echo     app fully closed and reopened ^(not just backgrounded^) to pick up
echo     the new version - see MANUAL.md chapter 12 if a device still shows
echo     an old version.
echo.
if not defined CHECK_ONLY pause
exit /b 0


:write_check_result
:: %1 = signature status (ok / failed / no-pubkey), %2 = whether an update is
:: waiting. Read by the server and shown in Settings, so the office PC says
:: "v3.1 is available" rather than nobody finding out for a season.
::
:: A file under data\, not a database row: this runs as a separate process
:: from the server, data\ is gitignored so an update cannot overwrite it, and
:: SystemSetting is served publicly to every unauthenticated device and blanked
:: field-by-field on every settings Save.
::
:: Failures are written too, on purpose. A check that has not succeeded since
:: April looks exactly like "no updates available" unless it says so.
set "CHECK_STAMP="
for /f "delims=" %%S in ('powershell -NoProfile -Command "Get-Date -Format o" 2^>nul') do set "CHECK_STAMP=%%S"
set "RESULT_FILE=%~dp0data\update_available.json"
set "RESULT_TMP=%~dp0data\update_available.tmp"
> "!RESULT_TMP!" (
    echo {
    echo   "checked_at": "!CHECK_STAMP!",
    echo   "current": "!CURTAG!",
    echo   "latest": "!NEWTAG!",
    echo   "signature": "%~1",
    echo   "update_available": %~2
    echo }
)
:: Renamed rather than written in place, so the server never reads a file that
:: is half-written.
move /y "!RESULT_TMP!" "!RESULT_FILE!" >nul
exit /b 0
