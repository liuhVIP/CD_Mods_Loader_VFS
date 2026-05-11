# cdloader pyc 发布包打包脚本。
param(
    # Python 解释器路径，默认使用项目虚拟环境。
    [string]$PythonPath = "",

    # 发布输出目录，默认写入 dist。
    [string]$DistDir = "dist",

    # 发布包目录名，最终目录为 dist/cdloader_pyc_release。
    [string]$PackageName = "cdloader_pyc_release",

    # 只生成目录，不生成 zip 压缩包。
    [switch]$NoZip
)

$ErrorActionPreference = "Stop"

# 当前脚本所在目录，也就是 cdmm 包源码目录。
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# 项目根目录，源码运行时 cdmm 包位于该目录下。
$RepoRoot = Split-Path -Parent $ScriptDir

# 临时源码复制目录，用于避免在真实源码旁边生成 legacy pyc。
$BuildRoot = Join-Path $ScriptDir "build\pyc_release"

# 临时可编译包目录。
$CompilePackageDir = Join-Path $BuildRoot "source\cdmm"

# 发布根目录，用户最终解压后直接放在游戏根目录使用。
$OutputRoot = Join-Path (Join-Path $ScriptDir $DistDir) $PackageName

# 发布包内的 cdmm 字节码包目录。
$OutputPackageDir = Join-Path $OutputRoot "cdmm"

# 需要编译进发布包的核心源码文件和业务模块目录。
$RootPythonFiles = @(
    "__init__.py",
    "cli.py"
)

# 核心业务模块目录，新增业务目录时应同步加入这里。
$PackageDirectories = @(
    "archive",
    "common",
    "config",
    "io",
    "services",
    "storage",
    "utils"
)

if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    $PythonPath = Join-Path $ScriptDir ".venv\Scripts\python.exe"
}

if (-not (Test-Path -LiteralPath $PythonPath)) {
    Write-Host "未找到 Python 解释器：$PythonPath" -ForegroundColor Red
    exit 1
}

$PythonVersionInfo = & $PythonPath -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}|{sys.version_info.major}{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($PythonVersionInfo)) {
    Write-Host "无法检测 Python 版本：$PythonPath" -ForegroundColor Red
    exit 1
}
$PythonVersionParts = $PythonVersionInfo.Trim().Split("|")
$PythonMajorMinor = $PythonVersionParts[0]
$PythonPyLauncherVersion = "-$PythonMajorMinor"

Set-Location -LiteralPath $RepoRoot

Write-Host "准备 pyc 发布包目录..." -ForegroundColor Cyan
Remove-Item -LiteralPath $BuildRoot -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $OutputRoot -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $CompilePackageDir -Force | Out-Null
New-Item -ItemType Directory -Path $OutputPackageDir -Force | Out-Null

foreach ($FileName in $RootPythonFiles) {
    $SourcePath = Join-Path $ScriptDir $FileName
    if (-not (Test-Path -LiteralPath $SourcePath)) {
        Write-Host "缺少核心源码文件：$SourcePath" -ForegroundColor Red
        exit 1
    }
    Copy-Item -LiteralPath $SourcePath -Destination (Join-Path $CompilePackageDir $FileName) -Force
}

foreach ($DirectoryName in $PackageDirectories) {
    $SourcePath = Join-Path $ScriptDir $DirectoryName
    $TargetPath = Join-Path $CompilePackageDir $DirectoryName
    if (-not (Test-Path -LiteralPath $SourcePath)) {
        Write-Host "缺少核心模块目录：$SourcePath" -ForegroundColor Red
        exit 1
    }
    Copy-Item -LiteralPath $SourcePath -Destination $TargetPath -Recurse -Force
}

Get-ChildItem -LiteralPath $CompilePackageDir -Recurse -Directory -Filter "__pycache__" |
    Remove-Item -Recurse -Force

Write-Host "编译核心代码为 legacy pyc..." -ForegroundColor Cyan
$env:CDLOADER_PYC_COMPILE_ROOT = $CompilePackageDir
$CompileCode = @'
from __future__ import annotations

import compileall
import os
import py_compile
import sys
from pathlib import Path

compile_root = Path(os.environ["CDLOADER_PYC_COMPILE_ROOT"])
ok = compileall.compile_dir(
    str(compile_root),
    maxlevels=20,
    ddir="cdmm",
    force=True,
    quiet=1,
    legacy=True,
    optimize=2,
    invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
)
raise SystemExit(0 if ok else 1)
'@

$CompileCode | & $PythonPath -
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host "复制 pyc 字节码到发布包..." -ForegroundColor Cyan
$CompilePackageFullPath = (Resolve-Path -LiteralPath $CompilePackageDir).Path
Get-ChildItem -LiteralPath $CompilePackageDir -Recurse -File -Filter "*.pyc" | ForEach-Object {
    $RelativePath = $_.FullName.Substring($CompilePackageFullPath.Length).TrimStart("\", "/")
    $TargetPath = Join-Path $OutputPackageDir $RelativePath
    $TargetDirectory = Split-Path -Parent $TargetPath
    New-Item -ItemType Directory -Path $TargetDirectory -Force | Out-Null
    Copy-Item -LiteralPath $_.FullName -Destination $TargetPath -Force
}

# 发布包允许玩家通过 cdmm/config/game_config.json 指定游戏目录。
$OutputConfigDir = Join-Path $OutputPackageDir "config"
New-Item -ItemType Directory -Path $OutputConfigDir -Force | Out-Null
$ReleaseGameConfig = @'
{
  "game_dir": ""
}
'@
Set-Content -LiteralPath (Join-Path $OutputConfigDir "game_config.json") -Value $ReleaseGameConfig -Encoding utf8

Write-Host "写入发布包启动脚本..." -ForegroundColor Cyan
$LauncherPs1 = @'
# cdloader pyc 发布包启动脚本。
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$LoaderArgs = @()
)

$ErrorActionPreference = "Stop"

# 当前脚本所在目录，可以是 Crimson Desert 游戏根目录，也可以是独立工具目录。
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# 本地 Python 虚拟环境目录，由 uv 自动创建。
$VenvDir = Join-Path $ScriptDir ".cdloader_python"

# 本地虚拟环境 Python。
$VenvPythonPath = Join-Path $VenvDir "Scripts\python.exe"

# 运行依赖清单。
$RequirementsPath = Join-Path $ScriptDir "cdloader_requirements.txt"

# 可选游戏目录配置文件。不把发布包解压到游戏根目录时，编辑这里。
$GameConfigPath = Join-Path $ScriptDir "cdmm\config\game_config.json"

# 当前 pyc 包要求的 Python 小版本。
$RequiredPythonVersion = "__PYTHON_MAJOR_MINOR__"

# Python 模块搜索路径，确保 cdmm/*.pyc 可以被导入。
$env:PYTHONPATH = if ([string]::IsNullOrWhiteSpace($env:PYTHONPATH)) {
    $ScriptDir
} else {
    "$ScriptDir;$env:PYTHONPATH"
}

function Test-GameRoot {
    param(
        [string]$Path
    )
    # 游戏根目录必须包含官方主程序。
    return Test-Path -LiteralPath (Join-Path $Path "bin64\CrimsonDesert.exe")
}

function Read-ConfiguredGameDir {
    # 读取 cdmm/config/game_config.json 中的 game_dir。
    if (-not (Test-Path -LiteralPath $GameConfigPath)) {
        return $null
    }
    try {
        $Config = Get-Content -LiteralPath $GameConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($null -ne $Config.game_dir -and -not [string]::IsNullOrWhiteSpace([string]$Config.game_dir)) {
            return [string]$Config.game_dir
        }
    } catch {
        Write-Host "读取 game_config.json 失败：$($_.Exception.Message)" -ForegroundColor Yellow
    }
    return $null
}

function Resolve-GameDir {
    # 优先使用当前目录，其次读取 cdmm/config/game_config.json。
    if (Test-GameRoot -Path $ScriptDir) {
        return (Resolve-Path -LiteralPath $ScriptDir).Path
    }

    $ConfiguredGameDir = Read-ConfiguredGameDir
    if (-not [string]::IsNullOrWhiteSpace($ConfiguredGameDir)) {
        $ExpandedGameDir = [Environment]::ExpandEnvironmentVariables($ConfiguredGameDir.Trim().Trim('"'))
        if (Test-GameRoot -Path $ExpandedGameDir) {
            return (Resolve-Path -LiteralPath $ExpandedGameDir).Path
        }
        Write-Host "game_config.json 中的游戏目录无效：$ExpandedGameDir" -ForegroundColor Yellow
    }

    return $null
}

function Test-HasGameDirArg {
    param(
        [string[]]$Args
    )
    return $Args -contains "--game-dir"
}

function Test-PythonCommand {
    param(
        [string]$Exe,
        [string[]]$Args
    )
    & $Exe @Args -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (__PYC_MAJOR_MINOR_TUPLE__) else 1)" *> $null
    return $LASTEXITCODE -eq 0
}

function Resolve-UvCommand {
    # 查找已安装的 uv。
    $UvCommand = Get-Command "uv" -ErrorAction SilentlyContinue
    if ($null -ne $UvCommand) {
        return $UvCommand.Source
    }

    $Candidates = @(
        (Join-Path $env:USERPROFILE ".local\bin\uv.exe"),
        (Join-Path $env:USERPROFILE ".cargo\bin\uv.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\uv\uv.exe")
    )
    foreach ($Candidate in $Candidates) {
        if (-not [string]::IsNullOrWhiteSpace($Candidate) -and (Test-Path -LiteralPath $Candidate)) {
            return $Candidate
        }
    }

    return $null
}

function Install-Uv {
    # 自动安装 uv，安装过程需要联网。
    Write-Host "未检测到 uv，正在尝试自动安装 uv..." -ForegroundColor Yellow
    $InstallCommand = "irm https://astral.sh/uv/install.ps1 | iex"
    & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command $InstallCommand
    if ($LASTEXITCODE -ne 0) {
        return $null
    }
    return Resolve-UvCommand
}

function Ensure-UvCommand {
    # 确保 uv 可用。
    $Uv = Resolve-UvCommand
    if ($null -ne $Uv) {
        return $Uv
    }
    return Install-Uv
}

function Test-RuntimeDependencies {
    param(
        [string]$PythonExe
    )
    & $PythonExe -c "import cryptography; import lz4.block" *> $null
    return $LASTEXITCODE -eq 0
}

function Install-RuntimeDependencies {
    param(
        [string]$Uv,
        [string]$PythonExe
    )
    # 使用 uv 为本地虚拟环境安装运行依赖。
    if (-not (Test-Path -LiteralPath $RequirementsPath)) {
        Write-Host "缺少依赖清单：$RequirementsPath" -ForegroundColor Red
        return $false
    }
    & $Uv "pip" "install" "--python" $PythonExe "-r" $RequirementsPath
    return $LASTEXITCODE -eq 0
}

function Ensure-LocalPython {
    # 使用 uv 自动安装 Python 小版本并创建本地虚拟环境。
    if ((Test-Path -LiteralPath $VenvPythonPath) -and (Test-PythonCommand -Exe $VenvPythonPath -Args @())) {
        if (Test-RuntimeDependencies -PythonExe $VenvPythonPath) {
            return $VenvPythonPath
        }
    }

    $Uv = Ensure-UvCommand
    if ($null -eq $Uv) {
        Write-Host "uv 安装失败，请手动安装 uv 后重新运行。" -ForegroundColor Red
        return $null
    }

    Write-Host "正在准备 Python $RequiredPythonVersion.x 运行环境..." -ForegroundColor Cyan
    & $Uv "python" "install" $RequiredPythonVersion
    if ($LASTEXITCODE -ne 0) {
        Write-Host "uv 安装 Python $RequiredPythonVersion.x 失败。" -ForegroundColor Red
        return $null
    }

    if (Test-Path -LiteralPath $VenvDir) {
        Remove-Item -LiteralPath $VenvDir -Recurse -Force
    }

    & $Uv "venv" "--python" $RequiredPythonVersion $VenvDir
    if ($LASTEXITCODE -ne 0) {
        Write-Host "uv 创建本地 Python 环境失败。" -ForegroundColor Red
        return $null
    }

    if (-not (Install-RuntimeDependencies -Uv $Uv -PythonExe $VenvPythonPath)) {
        Write-Host "运行依赖安装失败。" -ForegroundColor Red
        return $null
    }

    return $VenvPythonPath
}

function Resolve-PythonExe {
    # 优先使用用户显式指定的 Python。
    if (-not [string]::IsNullOrWhiteSpace($env:CDLOADER_PYTHON) -and (Test-Path -LiteralPath $env:CDLOADER_PYTHON)) {
        if (Test-PythonCommand -Exe $env:CDLOADER_PYTHON -Args @()) {
            if (Test-RuntimeDependencies -PythonExe $env:CDLOADER_PYTHON) {
                return $env:CDLOADER_PYTHON
            }
        }
    }

    if ((Test-Path -LiteralPath $VenvPythonPath) -and (Test-PythonCommand -Exe $VenvPythonPath -Args @())) {
        if (Test-RuntimeDependencies -PythonExe $VenvPythonPath) {
            return $VenvPythonPath
        }
    }

    return Ensure-LocalPython
}

function Invoke-Cdloader {
    param(
        [string]$PythonExe,
        [string[]]$Args
    )
    Set-Location -LiteralPath $ScriptDir
    & $PythonExe -m cdmm.cli @Args
    return $LASTEXITCODE
}

function Invoke-CdloaderCommand {
    param(
        [string]$PythonExe,
        [string]$GameDir,
        [string]$Command
    )
    return Invoke-Cdloader -PythonExe $PythonExe -Args @($Command, "--game-dir", $GameDir)
}

function Pause-Exit {
    Read-Host "按 Enter 退出"
}

Write-Host "红色沙漠独立轻量模组加载器 - pyc 发布版" -ForegroundColor Cyan

$PythonExe = Resolve-PythonExe
if ($null -eq $PythonExe) {
    Write-Host "未能准备 Python __PYTHON_MAJOR_MINOR__.x 运行环境。" -ForegroundColor Red
    Write-Host "pyc 文件与 Python 小版本绑定，本发布包需要 Python __PYTHON_MAJOR_MINOR__.x。"
    Pause-Exit
    exit 1
}

if ($LoaderArgs.Count -gt 0) {
    if ($LoaderArgs -contains "--help" -or $LoaderArgs -contains "-h" -or $LoaderArgs -contains "--game-dir") {
        exit (Invoke-Cdloader -PythonExe $PythonExe -Args $LoaderArgs)
    }
    $GameDir = Resolve-GameDir
    if ($null -eq $GameDir) {
        Write-Host "未找到 Crimson Desert 游戏根目录。" -ForegroundColor Red
        Write-Host "请把发布包解压到游戏根目录，或编辑 cdmm\config\game_config.json 中的 game_dir。"
        Pause-Exit
        exit 1
    }
    exit (Invoke-Cdloader -PythonExe $PythonExe -Args ($LoaderArgs + @("--game-dir", $GameDir)))
}

$GameDir = Resolve-GameDir
if ($null -eq $GameDir) {
    Write-Host "未找到 Crimson Desert 游戏根目录。" -ForegroundColor Red
    Write-Host "请把发布包解压到游戏根目录，或编辑 cdmm\config\game_config.json 中的 game_dir。"
    Pause-Exit
    exit 1
}

$ExitCode = 0
while ($true) {
    Write-Host ""
    Write-Host "1. 开始加载模组"
    Write-Host "2. 只扫描 mods，不写入游戏文件"
    Write-Host "3. 退出"
    $Choice = Read-Host "请选择"
    if ([string]::IsNullOrWhiteSpace($Choice)) {
        $Choice = "1"
    }

    switch ($Choice) {
        "1" { $ExitCode = Invoke-CdloaderCommand -PythonExe $PythonExe -GameDir $GameDir -Command "apply"; Read-Host "按 Enter 返回菜单" | Out-Null }
        "2" { $ExitCode = Invoke-CdloaderCommand -PythonExe $PythonExe -GameDir $GameDir -Command "scan"; Read-Host "按 Enter 返回菜单" | Out-Null }
        "3" { exit $ExitCode }
        default { Write-Host "无效选择，请重新输入。" -ForegroundColor Yellow }
    }
}
'@

$LauncherBat = @'
@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
set "REQUIRED_PYTHON=__PYTHON_MAJOR_MINOR__"
set "VENV_DIR=%SCRIPT_DIR%\.cdloader_python"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"
set "REQ_FILE=%SCRIPT_DIR%\cdloader_requirements.txt"
set "PYTHONPATH=%SCRIPT_DIR%;%PYTHONPATH%"
set "CDLOADER_LAUNCH_DIR=%SCRIPT_DIR%"
set "UV_LINK_MODE=copy"

echo Crimson Desert Lightweight Mod Loader - pyc release
echo.

call :ensure_python
if errorlevel 1 (
  echo.
  echo Failed to prepare the Python runtime.
  pause
  exit /b 1
)

echo Starting loader...
echo.
"%VENV_PYTHON%" -m cdmm.cli %*
exit /b %ERRORLEVEL%

:ensure_python
if exist "%VENV_PYTHON%" (
  call :check_python "%VENV_PYTHON%"
  if not errorlevel 1 (
    call :check_deps "%VENV_PYTHON%"
    if not errorlevel 1 exit /b 0
  )
)

call :ensure_uv
if errorlevel 1 exit /b 1

echo Preparing Python %REQUIRED_PYTHON%.x runtime...
"%UV_EXE%" python install %REQUIRED_PYTHON%
if errorlevel 1 exit /b 1

if exist "%VENV_DIR%" rmdir /s /q "%VENV_DIR%"
"%UV_EXE%" venv --python %REQUIRED_PYTHON% "%VENV_DIR%"
if errorlevel 1 exit /b 1

if not exist "%REQ_FILE%" (
  echo Missing dependency file: %REQ_FILE%
  exit /b 1
)

"%UV_EXE%" pip install --python "%VENV_PYTHON%" -r "%REQ_FILE%"
if errorlevel 1 exit /b 1

call :check_python "%VENV_PYTHON%"
if errorlevel 1 exit /b 1
call :check_deps "%VENV_PYTHON%"
if errorlevel 1 exit /b 1
exit /b 0

:check_python
set "PY_EXE=%~1"
"%PY_EXE%" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (__PYC_MAJOR_MINOR_TUPLE__) else 1)" >nul 2>nul
exit /b %ERRORLEVEL%

:check_deps
set "PY_EXE=%~1"
"%PY_EXE%" -c "import cryptography; import lz4.block" >nul 2>nul
exit /b %ERRORLEVEL%

:ensure_uv
set "UV_EXE="
for /f "delims=" %%I in ('where uv 2^>nul') do (
  if not defined UV_EXE set "UV_EXE=%%I"
)
if defined UV_EXE exit /b 0

if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV_EXE=%USERPROFILE%\.local\bin\uv.exe"
if defined UV_EXE exit /b 0
if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "UV_EXE=%USERPROFILE%\.cargo\bin\uv.exe"
if defined UV_EXE exit /b 0
if exist "%LOCALAPPDATA%\Programs\uv\uv.exe" set "UV_EXE=%LOCALAPPDATA%\Programs\uv\uv.exe"
if defined UV_EXE exit /b 0

echo uv was not found. Installing uv now...
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
if errorlevel 1 exit /b 1

for /f "delims=" %%I in ('where uv 2^>nul') do (
  if not defined UV_EXE set "UV_EXE=%%I"
)
if defined UV_EXE exit /b 0
if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV_EXE=%USERPROFILE%\.local\bin\uv.exe"
if defined UV_EXE exit /b 0
if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "UV_EXE=%USERPROFILE%\.cargo\bin\uv.exe"
if defined UV_EXE exit /b 0

echo uv installation finished, but uv.exe was not found in PATH.
exit /b 1
'@

$RuntimeRequirements = @'
cryptography>=42.0
lz4>=4.3
'@

$LocalExeBuilderBat = @'
@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
set "REQUIRED_PYTHON=__PYTHON_MAJOR_MINOR__"
set "VENV_DIR=%SCRIPT_DIR%\.cdloader_python"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"
set "REQ_FILE=%SCRIPT_DIR%\cdloader_requirements.txt"
set "ENTRY_FILE=%SCRIPT_DIR%\cdloader_entry.py"
set "DIST_DIR=%SCRIPT_DIR%\local_exe"
set "BUILD_DIR=%SCRIPT_DIR%\build\pyinstaller_local"
set "SPEC_DIR=%SCRIPT_DIR%\build"
set "PYTHONPATH=%SCRIPT_DIR%;%PYTHONPATH%"
set "CDLOADER_LAUNCH_DIR=%SCRIPT_DIR%"
set "UV_LINK_MODE=copy"

echo Crimson Desert Lightweight Mod Loader - local exe builder
echo.
echo This script builds cdloader.exe on your own PC.
echo Nexus Mods does not receive an exe file from the mod author.
echo.

call :ensure_python
if errorlevel 1 (
  echo.
  echo Failed to prepare the Python runtime.
  pause
  exit /b 1
)

call :ensure_uv
if errorlevel 1 (
  echo Failed to prepare uv.
  pause
  exit /b 1
)

echo Installing PyInstaller...
"%UV_EXE%" pip install --python "%VENV_PYTHON%" "pyinstaller>=6.0"
if errorlevel 1 (
  echo Failed to install PyInstaller.
  pause
  exit /b 1
)

echo Writing temporary entry file...
> "%ENTRY_FILE%" echo from cdmm.cli import main
>> "%ENTRY_FILE%" echo raise SystemExit(main())

if exist "%DIST_DIR%" rmdir /s /q "%DIST_DIR%"
if exist "%BUILD_DIR%" rmdir /s /q "%BUILD_DIR%"

echo Building local cdloader.exe...
"%VENV_PYTHON%" -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --console ^
  --name cdloader ^
  --distpath "%DIST_DIR%" ^
  --workpath "%BUILD_DIR%" ^
  --specpath "%SPEC_DIR%" ^
  --optimize 2 ^
  "%ENTRY_FILE%"

set "BUILD_EXIT=%ERRORLEVEL%"
del "%ENTRY_FILE%" >nul 2>nul

if not "%BUILD_EXIT%"=="0" (
  echo.
  echo Build failed.
  pause
  exit /b %BUILD_EXIT%
)

echo.
echo Build completed:
echo %DIST_DIR%\cdloader.exe
echo.
echo Copy cdloader.exe to your Crimson Desert game root folder and run it.
pause
exit /b 0

:ensure_python
if exist "%VENV_PYTHON%" (
  call :check_python "%VENV_PYTHON%"
  if not errorlevel 1 (
    call :check_deps "%VENV_PYTHON%"
    if not errorlevel 1 exit /b 0
  )
)

call :ensure_uv
if errorlevel 1 exit /b 1

echo Preparing Python %REQUIRED_PYTHON%.x runtime...
"%UV_EXE%" python install %REQUIRED_PYTHON%
if errorlevel 1 exit /b 1

if exist "%VENV_DIR%" rmdir /s /q "%VENV_DIR%"
"%UV_EXE%" venv --python %REQUIRED_PYTHON% "%VENV_DIR%"
if errorlevel 1 exit /b 1

if not exist "%REQ_FILE%" (
  echo Missing dependency file: %REQ_FILE%
  exit /b 1
)

"%UV_EXE%" pip install --python "%VENV_PYTHON%" -r "%REQ_FILE%"
if errorlevel 1 exit /b 1

call :check_python "%VENV_PYTHON%"
if errorlevel 1 exit /b 1
call :check_deps "%VENV_PYTHON%"
if errorlevel 1 exit /b 1
exit /b 0

:check_python
set "PY_EXE=%~1"
"%PY_EXE%" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (__PYC_MAJOR_MINOR_TUPLE__) else 1)" >nul 2>nul
exit /b %ERRORLEVEL%

:check_deps
set "PY_EXE=%~1"
"%PY_EXE%" -c "import cryptography; import lz4.block" >nul 2>nul
exit /b %ERRORLEVEL%

:ensure_uv
set "UV_EXE="
for /f "delims=" %%I in ('where uv 2^>nul') do (
  if not defined UV_EXE set "UV_EXE=%%I"
)
if defined UV_EXE exit /b 0

if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV_EXE=%USERPROFILE%\.local\bin\uv.exe"
if defined UV_EXE exit /b 0
if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "UV_EXE=%USERPROFILE%\.cargo\bin\uv.exe"
if defined UV_EXE exit /b 0
if exist "%LOCALAPPDATA%\Programs\uv\uv.exe" set "UV_EXE=%LOCALAPPDATA%\Programs\uv\uv.exe"
if defined UV_EXE exit /b 0

echo uv was not found. Installing uv now...
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
if errorlevel 1 exit /b 1

for /f "delims=" %%I in ('where uv 2^>nul') do (
  if not defined UV_EXE set "UV_EXE=%%I"
)
if defined UV_EXE exit /b 0
if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV_EXE=%USERPROFILE%\.local\bin\uv.exe"
if defined UV_EXE exit /b 0
if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "UV_EXE=%USERPROFILE%\.cargo\bin\uv.exe"
if defined UV_EXE exit /b 0

echo uv installation finished, but uv.exe was not found in PATH.
exit /b 1
'@

$ReleaseReadme = @'
# Crimson Desert Lightweight Mod Loader

A standalone lightweight mod loader for Crimson Desert.

This tool is made for players who want a fast and simple way to load multiple Crimson Desert mods without manually replacing game files one by one. It scans your `mods` folder, recognizes supported mod layouts, and loads them through one unified process.

This release is the `.pyc` version. Core `.py` source files are not included.

## Main Features

- Lightweight standalone loader
- Small release package
- Fast startup after the first runtime setup
- Fast mod loading for repeated testing and mod combinations
- No full GUI mod manager required
- Automatically scans the `mods` folder
- Supports several common Crimson Desert mod layouts
- Supports scan-only mode before writing changes
- Keeps console output simple
- Writes detailed logs for troubleshooting
- Helps reduce common manual-install problems such as file overwrites and multi-mod conflicts

## Supported Mod Types

Current supported mod types include:

- Traditional JSON byte patch mods
- Loose file mods
- Numbered folder layouts such as `files/0000/...`
- Root numbered folder layouts such as `0000/...`
- Game-path loose files such as `files/gamedata/...`
- Root game paths such as `gamedata/...`, `ui/...`, and `sequencer/...`
- Standalone archive mods with `0.paz` and `0.pamt`
- DDS texture mods
- Selected supported Format 3 mods

## Format 3 Notice

Format 3 support is partial.

The loader supports implemented Format 3 cases, but it does not claim full Format 3 compatibility. Unsupported Format 3 fields may be skipped and reported in the logs.

## Recommended Setup

1. Download `cdloader_pyc_release.zip`.
2. Extract all files into your Crimson Desert game root folder.
3. The folder should contain:

```text
bin64\CrimsonDesert.exe
```

4. Put your mods in:

```text
mods
```

5. Run:

```bat
cdloader_pyc.bat
```

On first launch, the script prepares a local runtime environment automatically. Internet access may be required the first time.

## Alternative Setup

You can also keep the loader outside the game folder.

On first run, the program will ask for your Crimson Desert game root folder. After you enter a valid path, it will be saved automatically to:

```text
cdmm\config\game_config.json
```

You can also edit this file manually:

```json
{
  "game_dir": "G:\\SteamLibrary\\steamapps\\common\\Crimson Desert"
}
```

After that, you can simply run `cdloader_pyc.bat` again.

## Menu

When started without command-line arguments, the loader opens its normal console menu:

```text
1. Start loading mods
2. Scan mods only, without writing game files
3. Exit
```

Use scan-only mode if you want to check whether your installed mods are recognized before applying them.

## Command Line

You can also run commands directly:

```bat
cdloader_pyc.bat apply
cdloader_pyc.bat scan
cdloader_pyc.bat revert
```

## Runtime Setup

This package does not include an `.exe` file and does not include a full Python runtime.

Install Python __PYTHON_MAJOR_MINOR__.x first, then install the required packages in this folder:

```bat
python -m pip install -r cdloader_requirements.txt
```

After that, run:

```bat
cdloader_pyc.bat
```

The batch file only checks the local Python environment and starts the `.pyc` loader.

## Logs

Detailed logs are written under the game folder:

```text
.cdloader\logs
```

Common log files include:

```text
cold_load.log
hot_load.log
scan.log
```

If a mod does not work as expected, check these logs first.

## Important Notes

- Back up your game files before using mods.
- This loader does not guarantee that every mod is compatible with every other mod.
- Unsupported Format 3 data may be skipped.
- If something behaves strangely, try starting from a clean game folder and add mods one by one.

## About This Release

This is a `.pyc` release intended for Nexus Mods distribution. It avoids shipping plain `.py` source files and avoids uploading an executable file.

`.pyc` is not encryption. It only prevents direct source-code viewing. Advanced users may still be able to inspect or decompile it.
'@

$ReleaseReadmeZh = @'
# 红色沙漠独立轻量模组加载器

这是一个用于《红色沙漠》的独立轻量模组加载器。

它适合想要快速安装、测试和组合多个模组的玩家。加载器会扫描游戏目录下的 `mods` 文件夹，识别支持的模组结构，并通过统一流程加载这些模组，减少手动复制文件、覆盖文件和多模组冲突带来的麻烦。

本发布包是 `.pyc` 版本，不包含核心 `.py` 源码。

## 主要特点

- 独立轻量，不需要完整 GUI 模组管理器
- 发布包体积小
- 首次环境准备完成后，后续启动更快
- 加载速度快，适合频繁测试和组合多个模组
- 自动扫描 `mods` 文件夹
- 支持多种常见《红色沙漠》模组结构
- 支持只扫描不写入，方便先检查模组识别结果
- 控制台输出简洁
- 详细日志会写入文件，便于排查问题
- 尽量减少手动安装时常见的文件覆盖、多模组互相覆盖等问题

## 当前支持的模组类型

当前支持的模组类型包括：

- 传统 JSON byte patch 模组
- loose files 散装文件模组
- `files/0000/...` 编号目录结构
- 根部 `0000/...` 编号目录结构
- `files/gamedata/...` 游戏路径结构
- 根部 `gamedata/...`、`ui/...`、`sequencer/...` 等游戏路径文件
- 带有 `0.paz` 和 `0.pamt` 的 standalone archive 模组
- DDS 贴图模组
- 部分已实现支持的 Format 3 模组

## 关于 Format 3

Format 3 当前是部分支持。

加载器只支持已经实现的 Format 3 场景，不承诺完整支持所有 Format 3 模组。暂不支持的 Format 3 字段可能会被跳过，并写入日志。

## 推荐使用方式

1. 下载 `cdloader_pyc_release.zip`。
2. 将所有文件解压到《红色沙漠》游戏根目录。
3. 游戏根目录中应能看到：

```text
bin64\CrimsonDesert.exe
```

4. 将模组放入：

```text
mods
```

5. 双击运行：

```bat
cdloader_pyc.bat
```

首次运行时，脚本会自动准备本地运行环境。如果本机还没有相关环境，首次运行可能需要联网。

## 不放在游戏根目录时

你也可以把加载器放在任意目录。

第一次运行时，程序会提示输入《红色沙漠》游戏根目录。输入有效路径后，会自动保存到：

```text
cdmm\config\game_config.json
```

你也可以手动编辑这个文件：

```json
{
  "game_dir": "G:\\SteamLibrary\\steamapps\\common\\Crimson Desert"
}
```

保存后，再次运行 `cdloader_pyc.bat` 就不需要重复输入游戏目录。

## 菜单

不带命令行参数启动时，加载器会显示正常控制台菜单：

```text
1. 开始加载模组
2. 只扫描 mods，不写入游戏文件
3. 退出
```

如果只是想检查模组是否能被识别，可以先使用只扫描模式。

## 命令行用法

也可以直接运行命令：

```bat
cdloader_pyc.bat apply
cdloader_pyc.bat scan
cdloader_pyc.bat revert
```

## 运行环境

本发布包不包含 `.exe`，也不内置完整 Python 环境。

请先安装 Python __PYTHON_MAJOR_MINOR__.x，然后在本目录安装依赖：

```bat
python -m pip install -r cdloader_requirements.txt
```

之后运行：

```bat
cdloader_pyc.bat
```

这个 bat 只会检查本机 Python 环境并启动 `.pyc` 加载器。

## 日志

详细日志会写入游戏目录下：

```text
.cdloader\logs
```

常见日志文件包括：

```text
cold_load.log
hot_load.log
scan.log
```

如果模组没有生效，建议先查看这些日志。

## 注意事项

- 使用任何模组前都建议备份游戏文件。
- 本加载器不保证所有模组都能互相兼容。
- 暂不支持的 Format 3 数据可能会被跳过。
- 如果遇到异常，建议从纯净游戏目录开始，并逐个添加模组排查。

## 关于本发布包

这是面向 Nexus Mods 发布的 `.pyc` 版本，用于避免直接发布 `.py` 源码，同时也避免上传 exe。

`.pyc` 不是加密格式，只是避免直接查看源码。有经验的用户仍然可能反编译或分析它。
'@

$LauncherPs1 = $LauncherPs1.
    Replace("__PYC_MAJOR_MINOR_TUPLE__", (($PythonMajorMinor.Split(".") -join ", ") + ",")).
    Replace("__PY_LAUNCHER_VERSION__", $PythonPyLauncherVersion).
    Replace("__PYTHON_MAJOR_MINOR__", $PythonMajorMinor)
$LauncherBat = $LauncherBat.
    Replace("__PYC_MAJOR_MINOR_TUPLE__", (($PythonMajorMinor.Split(".") -join ", ") + ",")).
    Replace("__PYTHON_MAJOR_MINOR__", $PythonMajorMinor)
$LocalExeBuilderBat = $LocalExeBuilderBat.
    Replace("__PYC_MAJOR_MINOR_TUPLE__", (($PythonMajorMinor.Split(".") -join ", ") + ",")).
    Replace("__PYTHON_MAJOR_MINOR__", $PythonMajorMinor)
$ReleaseReadme = $ReleaseReadme.Replace("__PYTHON_MAJOR_MINOR__", $PythonMajorMinor)
$ReleaseReadmeZh = $ReleaseReadmeZh.Replace("__PYTHON_MAJOR_MINOR__", $PythonMajorMinor)

Set-Content -LiteralPath (Join-Path $OutputRoot "cdloader_pyc.bat") -Value $LauncherBat -Encoding ascii
Set-Content -LiteralPath (Join-Path $OutputRoot "build_local_exe.bat") -Value $LocalExeBuilderBat -Encoding ascii
Set-Content -LiteralPath (Join-Path $OutputRoot "cdloader_requirements.txt") -Value $RuntimeRequirements -Encoding ascii
Set-Content -LiteralPath (Join-Path $OutputRoot "README_PYC_RELEASE.md") -Value $ReleaseReadme -Encoding utf8
Set-Content -LiteralPath (Join-Path $OutputRoot "README_PYC_RELEASE_ZH.md") -Value $ReleaseReadmeZh -Encoding utf8

if (-not $NoZip) {
    $ZipPath = Join-Path (Join-Path $ScriptDir $DistDir) "$PackageName.zip"
    Remove-Item -LiteralPath $ZipPath -Force -ErrorAction SilentlyContinue
    Write-Host "生成 zip 发布包..." -ForegroundColor Cyan
    Compress-Archive -Path (Join-Path $OutputRoot "*") -DestinationPath $ZipPath -Force
    Write-Host "zip 发布包：$ZipPath" -ForegroundColor Green
}

Write-Host "pyc 发布目录：$OutputRoot" -ForegroundColor Green
