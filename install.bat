@echo off
:: Boord - double-click this file to install/set up
:: the server on this PC. See MANUAL.md chapter 2 for what it does and for
:: the manual step-by-step alternative.
::
:: Uses "-ExecutionPolicy Bypass" scoped to this one invocation only - it
:: does not change any system-wide setting. This is needed because Windows
:: blocks all PowerShell scripts from running by default, even legitimate
:: local ones like install.ps1.

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo This installer needs administrator rights - requesting them now...
    echo If Windows shows a User Account Control prompt, click "Yes".
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
echo.
pause
