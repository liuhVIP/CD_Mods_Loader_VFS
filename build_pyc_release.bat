@echo off
set "PWSH=C:\Program Files\PowerShell\7\pwsh.exe"
if exist "%PWSH%" (
  "%PWSH%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_pyc_release.ps1" %*
) else (
  powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_pyc_release.ps1" %*
)
exit /b %ERRORLEVEL%
