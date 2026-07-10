# cdloader VFS 专用 Nuitka 单体 exe 打包脚本。
param(
    [string]$PythonPath = "",
    [string]$DistDir = "dist_nuitka",
    [string]$OutputName = "cdloader-VFS-v1",
    [string]$RequiredPython = "3.10"
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# 固定项目根目录，避免从其他位置调用脚本时输出路径漂移。
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$HasCustomPythonPath = -not [string]::IsNullOrWhiteSpace($PythonPath)
if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    $PythonPath = Join-Path $ScriptDir ".venv-nuitka\Scripts\python.exe"
}

# VFS 专用输出目录和临时入口，和普通 cdloader 打包产物隔离。
$OutputDir = Join-Path $ScriptDir $DistDir
$BuildDir = Join-Path $ScriptDir "build\nuitka-vfs"
$EntryFile = Join-Path $BuildDir "cdloader_vfs_nuitka_entry.py"
$NuitkaVenvDir = Join-Path $ScriptDir ".venv-nuitka"
$VfsRuntimeDir = Join-Path $ScriptDir "private\vfs_runtime"
$VfsLauncherFile = Join-Path $VfsRuntimeDir "nppvfs_launcher.exe"
$VfsRuntimeDll = Join-Path $VfsRuntimeDir "vfs_runtime.dll"
$NativeDir = Join-Path $ScriptDir "native"

# Nuitka 构建依赖集中维护。
$NuitkaBuildPackages = @(
    "nuitka>=2.6",
    "ordered-set>=4.1",
    "zstandard>=0.22",
    "cryptography>=42",
    "lz4>=4.3"
)

function Find-UvExecutable {
    # 查找 uv 可执行文件，优先复用用户已有安装。
    $UvCommand = Get-Command uv -ErrorAction SilentlyContinue
    if ($null -ne $UvCommand) {
        return $UvCommand.Source
    }

    $UvCandidates = @(
        (Join-Path $env:USERPROFILE ".local\bin\uv.exe"),
        (Join-Path $env:USERPROFILE ".cargo\bin\uv.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\uv\uv.exe")
    )
    foreach ($UvPath in $UvCandidates) {
        if (Test-Path -LiteralPath $UvPath) {
            return $UvPath
        }
    }

    return ""
}

function Install-PythonPackages {
    param(
        [string]$TargetPython,
        [string[]]$Packages
    )

    # 安装 Python 包；优先使用 uv，兼容没有 pip 的虚拟环境。
    $UvPath = Find-UvExecutable
    if (-not [string]::IsNullOrWhiteSpace($UvPath)) {
        Write-Host "使用 uv 安装 Nuitka 构建依赖..." -ForegroundColor Cyan
        & $UvPath pip install --python $TargetPython @Packages
        return $LASTEXITCODE
    }

    Write-Host "未找到 uv，尝试为当前 Python 启用 pip..." -ForegroundColor Yellow
    & $TargetPython -m ensurepip --upgrade
    if ($LASTEXITCODE -ne 0) {
        return $LASTEXITCODE
    }

    & $TargetPython -m pip install --upgrade @Packages
    return $LASTEXITCODE
}

function Test-PythonVersion {
    param(
        [string]$TargetPython,
        [string]$VersionPrefix
    )

    # 校验 Python 小版本，避免 Nuitka 因版本不匹配触发额外编译器下载。
    & $TargetPython -c "import sys; raise SystemExit(0 if sys.version.startswith('$VersionPrefix.') else 1)" > $null 2>&1
    return $LASTEXITCODE -eq 0
}

function Ensure-NuitkaPython {
    param(
        [string]$TargetPython,
        [string]$VersionPrefix,
        [bool]$CustomPythonPath
    )

    # 用户显式传入 PythonPath 时尊重用户选择，但仍提醒版本不匹配的风险。
    if ($CustomPythonPath) {
        if (-not (Test-Path -LiteralPath $TargetPython)) {
            Write-Host "未找到 Python 解释器：$TargetPython" -ForegroundColor Red
            exit 1
        }
        if (-not (Test-PythonVersion -TargetPython $TargetPython -VersionPrefix $VersionPrefix)) {
            Write-Host "警告：当前 Python 不是 $VersionPrefix.x，Nuitka 可能需要额外下载编译器。" -ForegroundColor Yellow
        }
        return $TargetPython
    }

    if ((Test-Path -LiteralPath $TargetPython) -and (Test-PythonVersion -TargetPython $TargetPython -VersionPrefix $VersionPrefix)) {
        return $TargetPython
    }

    $UvPath = Find-UvExecutable
    if ([string]::IsNullOrWhiteSpace($UvPath)) {
        Write-Host "未找到 uv，无法自动创建 Python $VersionPrefix Nuitka 构建环境。" -ForegroundColor Red
        Write-Host "请安装 uv，或手动传入 -PythonPath 指向 Python $VersionPrefix.x。" -ForegroundColor Red
        exit 1
    }

    Write-Host "准备 Python $VersionPrefix.x 专用 Nuitka 构建环境..." -ForegroundColor Cyan
    & $UvPath python install $VersionPrefix
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    if (Test-Path -LiteralPath $NuitkaVenvDir) {
        Remove-Item -LiteralPath $NuitkaVenvDir -Recurse -Force
    }

    & $UvPath venv --python $VersionPrefix $NuitkaVenvDir
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    if (-not (Test-PythonVersion -TargetPython $TargetPython -VersionPrefix $VersionPrefix)) {
        Write-Host "Python $VersionPrefix Nuitka 构建环境创建后版本校验失败：$TargetPython" -ForegroundColor Red
        exit 1
    }

    return $TargetPython
}

if (-not (Test-Path -LiteralPath (Join-Path $ScriptDir "tools\vfs_launcher.py"))) {
    Write-Host "未找到 VFS 专用入口文件：$(Join-Path $ScriptDir "tools\vfs_launcher.py")" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path -LiteralPath $VfsLauncherFile)) {
    Write-Host "未找到闭源 VFS launcher：$VfsLauncherFile" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path -LiteralPath $VfsRuntimeDll)) {
    Write-Host "未找到闭源 VFS runtime：$VfsRuntimeDll" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path -LiteralPath (Join-Path $NativeDir "__init__.py"))) {
    Write-Host "未找到 native 包目录：$NativeDir" -ForegroundColor Red
    exit 1
}
if ((Get-ChildItem -LiteralPath $NativeDir -Filter "_cdloader_native*.pyd" -File).Count -eq 0) {
    Write-Host "未找到 _cdloader_native 原生扩展：$NativeDir" -ForegroundColor Red
    exit 1
}

$PythonPath = Ensure-NuitkaPython -TargetPython $PythonPath -VersionPrefix $RequiredPython -CustomPythonPath $HasCustomPythonPath

Write-Host "开始准备 VFS 专用 Nuitka 打包环境..." -ForegroundColor Cyan
$InstallExitCode = Install-PythonPackages -TargetPython $PythonPath -Packages $NuitkaBuildPackages
if ($InstallExitCode -ne 0) {
    exit $InstallExitCode
}

if (Test-Path -LiteralPath $BuildDir) {
    Remove-Item -LiteralPath $BuildDir -Recurse -Force
}

New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
New-Item -ItemType Directory -Path $BuildDir -Force | Out-Null

# 使用独立临时入口，保持 cdmm 包的绝对导入语义稳定。
@"
from cdmm.tools.vfs_launcher import main

raise SystemExit(main())
"@ | Set-Content -LiteralPath $EntryFile -Encoding UTF8

# 项目源码位于 cdmm 包目录本身，Nuitka 从父目录启动才能按 cdmm 包名解析绝对导入。
$ParentDir = Split-Path -Parent $ScriptDir
Set-Location $ParentDir

$NuitkaArgs = @(
    "-m",
    "nuitka",
    "--standalone",
    "--onefile",
    "--assume-yes-for-downloads",
    "--output-filename=$OutputName.exe",
    "--output-dir=$OutputDir",
    "--remove-output",
    "--windows-console-mode=force",
    "--company-name=cdmm",
    "--product-name=Crimson Desert VFS Mod Loader",
    "--file-description=Crimson Desert VFS Mod Loader",
    "--file-version=1.0.0.0",
    "--product-version=1.0.0.0",
    "--nofollow-import-to=pytest",
    "--nofollow-import-to=ruff",
    "--nofollow-import-to=PyInstaller",
    "--nofollow-import-to=pip",
    "--nofollow-import-to=setuptools",
    "--nofollow-import-to=wheel",
    "--nofollow-import-to=tkinter",
    "--nofollow-import-to=matplotlib",
    "--nofollow-import-to=IPython",
    "--nofollow-import-to=pandas",
    "--nofollow-import-to=numpy",
    "--nofollow-import-to=tqdm",
    "--include-module=cdmm.native._cdloader_native",
    "--include-package=cryptography",
    "--include-package=lz4",
    "--include-data-file=$VfsLauncherFile=cdmm/private/vfs_runtime/nppvfs_launcher.exe",
    "--include-data-file=$VfsRuntimeDll=cdmm/private/vfs_runtime/vfs_runtime.dll",
    "--jobs=$([Environment]::ProcessorCount)"
)

$NuitkaArgs += $EntryFile

Write-Host "开始使用 Nuitka 打包 cdloader-VFS-v1，不使用 UPX 压缩..." -ForegroundColor Cyan
& $PythonPath @NuitkaArgs
try {
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}
finally {
    Remove-Item -LiteralPath $EntryFile -Force -ErrorAction SilentlyContinue
}

$ExePath = Join-Path $OutputDir "$OutputName.exe"
if (-not (Test-Path -LiteralPath $ExePath)) {
    Write-Host "Nuitka 已结束，但未找到输出文件：$ExePath" -ForegroundColor Red
    exit 1
}

$ExeSize = (Get-Item -LiteralPath $ExePath).Length / 1MB
Write-Host ("VFS 专用 Nuitka 打包完成：{0}（{1:N2} MB）" -f $ExePath, $ExeSize) -ForegroundColor Green
Write-Host "复制 cdloader-VFS-v1.exe 到游戏根目录后双击，即默认构建 VFS 并启动游戏。" -ForegroundColor Green
