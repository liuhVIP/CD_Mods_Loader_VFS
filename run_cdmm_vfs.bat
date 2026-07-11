@echo off
set "PWSH=%ProgramFiles%\PowerShell\7\pwsh.exe"
if exist "%PWSH%" (
  "%PWSH%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_cdmm_vfs.ps1" %*
) else (
  powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_cdmm_vfs.ps1" %*
)
exit /b %ERRORLEVEL%
