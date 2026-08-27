@echo off
setlocal EnableDelayedExpansion
:: Boord - double-click this file to update to the newest signed release
:: from GitHub, install any new dependencies, refresh historical weather and
:: harvest data, and restart the server so the update actually takes effect.
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

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo This needs administrator rights to restart the server - requesting them now...
    echo If Windows shows a User Account Control prompt, click "Yes".
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

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
    pause
    exit /b 1
)
for /f "usebackq eol=# tokens=1 delims= " %%F in ("%FPR_FILE%") do (
    if not defined RELEASE_FPR set "RELEASE_FPR=%%F"
)
if not defined RELEASE_FPR (
    echo %FPR_FILE% is empty - expected a 40-character key fingerprint.
    echo Not updating. See MANUAL.md chapter 2, "Trusting the release key".
    pause
    exit /b 1
)
echo     Only releases signed by !RELEASE_FPR! will be accepted.

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
    pause
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
    pause
    exit /b 1
)

set "CURTAG=not on a release tag"
for /f "delims=" %%C in ('git describe --tags --exact-match --match "v*" HEAD 2^>nul') do set "CURTAG=%%C"

echo.
echo     Currently running: !CURTAG!
echo     Newest release:    !NEWTAG!

if "!CURTAG!"=="!NEWTAG!" (
    echo     Already on the newest signed release - skipping the code update.
) else (
    echo.
    echo ==^> Verifying the signature on !NEWTAG!...
    :: --raw prints GPG's machine-readable status lines. A VALIDSIG line means
    :: the signature is good; requiring OUR fingerprint on that line is the
    :: part that matters, because a plain "good signature" only proves the tag
    :: was signed by *some* key present in this machine's keyring.
    git verify-tag --raw "!NEWTAG!" 2>&1 | findstr /C:"VALIDSIG" | findstr /I /C:"!RELEASE_FPR!" >nul
    if errorlevel 1 (
        echo.
        echo *** SIGNATURE CHECK FAILED for !NEWTAG! ***
        echo.
        echo This release is not signed by the key this server trusts. That
        echo means one of:
        echo   - the release key was rotated and this server wasn't told
        echo   - the release genuinely wasn't signed
        echo   - someone tampered with the repository
        echo.
        echo Nothing has been changed. The server has NOT been restarted and
        echo is still running !CURTAG!. Do not work around this by checking
        echo the tag out by hand - find out why it failed first.
        pause
        exit /b 1
    )
    echo     Signature OK.

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
        pause
        exit /b 1
    )
)

echo.
echo ==^> Installing any new dependencies...
call "%~dp0backend\.venv\Scripts\activate.bat"
pip install --quiet --disable-pip-version-check -r "%~dp0backend\requirements.txt"
if %errorLevel% neq 0 (
    echo Warning: dependency install had a problem - continuing anyway, since
    echo most updates don't change requirements.txt. Check the error above if
    echo the server fails to start next.
)

echo.
echo ==^> Restarting the server...
schtasks /query /tn "Boord Server" >nul 2>&1
if %errorLevel% equ 0 (
    schtasks /end /tn "Boord Server" >nul 2>&1
    timeout /t 2 /nobreak >nul

    echo.
    echo ==^> Refreshing historical weather ^(2020-present^)...
    echo     This replaces recent days with Open-Meteo's finalized figures
    echo     ^(early readings start as provisional forecast-model values and
    echo     firm up over the following days/weeks^) - the Risk and Harvest
    echo     Forecast tabs depend on this being accurate, not just present.
    "%~dp0backend\.venv\Scripts\python.exe" "%~dp0scripts\import_historical_weather.py"
    if errorlevel 1 (
        echo Warning: weather refresh failed - check the error above ^(no
        echo internet, or Open-Meteo unreachable^). Starting the server
        echo anyway with whatever weather history it already had; it'll
        echo catch up on its own next time someone opens the Weather or
        echo Risk tab, or next time this script runs successfully.
    )

    echo.
    echo ==^> Refreshing older historical data ^(1987-2019^)...
    echo     Annual harvest totals ^(reads the farm's own OES workbook,
    echo     already in this checkout - no internet needed^) and older
    echo     weather ^(from Open-Meteo's archive API^). Both are finalized,
    echo     unchanging records, so re-running this every update is mostly
    echo     a no-op - it's here so a fresh/rebuilt server picks them up
    echo     automatically instead of needing the manual steps in
    echo     MANUAL.md chapter 2. Neither feeds the Risk indicator or
    echo     Harvest Forecast ^(both fixed to 2020-2025^) - only the
    echo     Historical Harvest Data report and Weather tab.
    "%~dp0backend\.venv\Scripts\python.exe" "%~dp0scripts\import_historical_annual_yield.py"
    if errorlevel 1 (
        echo Warning: older harvest data refresh failed - check the error
        echo above. Starting the server anyway with whatever it already had.
    )
    "%~dp0backend\.venv\Scripts\python.exe" "%~dp0scripts\import_historical_weather_archive.py"
    if errorlevel 1 (
        echo Warning: older weather refresh failed - check the error above
        echo ^(no internet, or Open-Meteo unreachable^). Starting the server
        echo anyway with whatever it already had.
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
pause
