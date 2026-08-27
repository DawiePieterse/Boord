@echo off
:: Boord - double-click this file to remove the server from this PC.
:: It unregisters the auto-start task and firewall rule and deletes the
:: virtual environment. It does NOT delete data\ - the database, worker
:: records, wages and backups - and it does not delete this folder. Those
:: stay a deliberate decision; uninstall.ps1 prints the exact commands.
::
:: Uses "-ExecutionPolicy Bypass" scoped to this one invocation only - it
:: does not change any system-wide setting.

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo This uninstaller needs administrator rights - requesting them now...
    echo If Windows shows a User Account Control prompt, click "Yes".
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1"
echo.
pause
