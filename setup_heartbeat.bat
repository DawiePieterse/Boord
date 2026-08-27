@echo off
:: Boord - double-click to set up uptime alerting.
:: Registers a Scheduled Task that runs heartbeat.ps1 every 10 minutes.
:: See MANUAL.md chapter 2, "Uptime alerting", for the healthchecks.io
:: account setup this depends on - do that first (including creating
:: heartbeat_url.txt next to this script), then run this once.

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo This needs administrator rights to register the scheduled task - requesting them now...
    echo If Windows shows a User Account Control prompt, click "Yes".
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

if not exist "%~dp0heartbeat_url.txt" (
    echo.
    echo heartbeat_url.txt not found next to this script.
    echo See MANUAL.md chapter 2, "Uptime alerting", to create your
    echo healthchecks.io check and save its ping URL there first.
    echo.
    pause
    exit /b 1
)

schtasks /query /tn "Boord Heartbeat" >nul 2>&1
if %errorLevel% equ 0 (
    schtasks /delete /tn "Boord Heartbeat" /f >nul 2>&1
)
schtasks /create /tn "Boord Heartbeat" /tr "powershell -NoProfile -ExecutionPolicy Bypass -File \"%~dp0heartbeat.ps1\"" /sc minute /mo 10 /ru SYSTEM /rl highest /f >nul 2>&1
if %errorLevel% neq 0 (
    echo.
    echo Failed to register the scheduled task - see the error above.
    pause
    exit /b 1
)

echo.
echo Done - "Boord Heartbeat" is registered and will run every
echo 10 minutes from now on, checking in with healthchecks.io as long as
echo the server responds.
echo.
pause
