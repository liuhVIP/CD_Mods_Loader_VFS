@echo off
set "PWSH=%ProgramFiles%\PowerShell\7\pwsh.exe"
if exist "%PWSH%" (
  "%PWSH%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_cdloader_nuitka.ps1" %*
) else (
  powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_cdloader_nuitka.ps1" %*
)
exit /b %ERRORLEVEL%
