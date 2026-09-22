@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-Career-Agent.ps1" -Demo %*
if errorlevel 1 pause
