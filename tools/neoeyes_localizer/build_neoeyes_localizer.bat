@echo off
setlocal
"C:\Program Files\PowerShell\7\pwsh.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_neoeyes_localizer.ps1"
exit /b %errorlevel%
