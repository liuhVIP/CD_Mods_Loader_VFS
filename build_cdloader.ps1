# cdloader 单体 exe 打包脚本。
param(
    [string]$PythonPath = "",
    [string]$DistDir = "dist",
    [string]$UpxPath = "E:\JetBrains\upx-4.2.4-win64\upx.exe",
    [switch]$SkipUpx
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    $PythonPath = Join-Path $ScriptDir ".venv\Scripts\python.exe"
}

if (-not (Test-Path -LiteralPath $PythonPath)) {
    Write-Host "未找到 Python 解释器：$PythonPath" -ForegroundColor Red
    exit 1
}

Set-Location $RepoRoot
$OutputDir = Join-Path $ScriptDir $DistDir
$WorkDir = Join-Path $ScriptDir "build\pyinstaller"
$SpecDir = Join-Path $ScriptDir "build"

# PyInstaller 会跟随部分可选依赖，把测试、打包、交互环境模块一起收进单文件。
# 这些模块不参与 cdloader 运行，集中排除可以降低成品 exe 体积。
$ExcludedModules = @(
    "pytest",
    "ruff",
    "PyInstaller",
    "pip",
    "wheel",
    "setuptools",
    "distutils",
    "unittest",
    "doctest",
    "pdb",
    "pydoc",
    "tkinter",
    "matplotlib",
    "IPython",
    "ipywidgets",
    "pandas",
    "numpy",
    "tqdm"
)

Write-Host "开始打包 cdloader 单体 exe..." -ForegroundColor Cyan
$PyInstallerArgs = @(
    "--noconfirm",
    "--clean",
    "--onefile",
    "--console",
    "--name", "cdloader",
    "--distpath", $OutputDir,
    "--workpath", $WorkDir,
    "--specpath", $SpecDir,
    "--optimize", "2",
    "--add-data", "$ScriptDir\config\game_config.json;config",
    "$ScriptDir\cli.py"
)

foreach ($ModuleName in $ExcludedModules) {
    $PyInstallerArgs = @("--exclude-module", $ModuleName) + $PyInstallerArgs
}

& $PythonPath -m PyInstaller @PyInstallerArgs

if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$ExePath = Join-Path $OutputDir "cdloader.exe"
if (-not $SkipUpx -and (Test-Path -LiteralPath $UpxPath)) {
    $BeforeUpxSize = (Get-Item -LiteralPath $ExePath).Length
    Write-Host "开始 UPX 压缩：$UpxPath" -ForegroundColor Cyan
    & $UpxPath --best --lzma --force $ExePath
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
    $AfterUpxSize = (Get-Item -LiteralPath $ExePath).Length
    $SavedSize = $BeforeUpxSize - $AfterUpxSize
    Write-Host ("UPX 完成：{0:N2} MB -> {1:N2} MB，减少 {2:N2} MB" -f ($BeforeUpxSize / 1MB), ($AfterUpxSize / 1MB), ($SavedSize / 1MB)) -ForegroundColor Green
} elseif (-not $SkipUpx) {
    Write-Host "未找到 UPX，跳过二次压缩：$UpxPath" -ForegroundColor Yellow
}

Write-Host "打包完成：$ExePath" -ForegroundColor Green
