@echo off
"C:\Program Files\PowerShell\7\pwsh.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0convert_all_mods_to_cdmod.ps1" %*
exit /b %ERRORLEVEL%
