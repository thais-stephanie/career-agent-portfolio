@echo off
rem Makes the "Career Agent" shortcut again (Desktop and Start menu), for this folder.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-Career-Agent.ps1" -Shortcuts
pause
