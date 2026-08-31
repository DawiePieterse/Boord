@echo off
setlocal EnableDelayedExpansion
:: Boord - double-click to be told when a new release is out.
::
:: Registers a Scheduled Task that runs "update_server.bat --check" once a
:: day. That looks for a newer signed release and writes what it found to
:: data\update_available.json, which the app reads and shows in
:: Settings -> Server. It does NOT install anything.
::
:: Installing stays a deliberate double-click of update_server.bat by a
:: person. update_server.bat runs the database migration in the foreground
:: precisely so a schema change happens in front of whoever chose to update,
:: instead of inside a Scheduled Task nobody is watching - and an update that
:: applies itself at 03:00 needs a rollback story to match, which this does
:: not have. The cost of getting that wrong is a pack house that cannot
:: receive fruit at six in the morning with nobody who knows why.
::
:: NOTE: this task runs as YOU, not as SYSTEM - unlike the server and the
:: heartbeat. It has to. Fetching from GitHub uses the SSH deploy key in
:: %USERPROFILE%\.ssh (MANUAL.md chapter 2), and SYSTEM has a different
:: profile with no key in it, so a check running as SYSTEM would fail
:: "Permission denied (publickey)" every day, silently, forever. The
:: consequence is that the check only runs while you are logged on, which is
:: also the only time anyone could act on what it finds.

cd /d "%~dp0"

echo.
echo ==^> Checking this PC can reach GitHub as you...
git ls-remote --tags origin >nul 2>&1
if errorlevel 1 (
    echo.
    echo Could not read the repository from this account.
    echo.
    echo The daily check runs as the logged-on user, so it needs the deploy
    echo key set up for THIS Windows account. See MANUAL.md chapter 2,
    echo "Cloning from GitHub", steps B to D. Test it by hand with:
    echo     git ls-remote origin
    echo.
    echo Nothing has been registered.
    echo.
    pause
    exit /b 1
)
echo     GitHub is reachable.

schtasks /query /tn "Boord Update Check" >nul 2>&1
if %errorLevel% equ 0 (
    schtasks /delete /tn "Boord Update Check" /f >nul 2>&1
)
schtasks /create /tn "Boord Update Check" /tr "\"%~dp0update_server.bat\" --check" /sc daily /st 07:30 /f >nul 2>&1
if %errorLevel% neq 0 (
    echo.
    echo Failed to register the scheduled task - see the error above.
    echo.
    echo If Windows asked for a password, register it to run only when you
    echo are logged on instead:
    echo     schtasks /create /tn "Boord Update Check" /tr "\"%~dp0update_server.bat\" --check" /sc onlogon /f
    echo.
    pause
    exit /b 1
)

echo.
echo ==^> Running the check once now, so you can see what it does...
call "%~dp0update_server.bat" --check

echo.
echo Done - "Boord Update Check" will run every day at 07:30 while you are
echo logged on. When a new release is out, Settings -^> Server in the admin
echo app will say so. Installing it is still up to you: double-click
echo update_server.bat when it suits the pack house.
echo.
pause
