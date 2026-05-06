# cdloader 单体 exe 打包脚本。
param(
    [string]$PythonPath = "",
    [string]$DistDir = "dist"
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

Write-Host "开始打包 cdloader 单体 exe..." -ForegroundColor Cyan
& $PythonPath -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --console `
    --name cdloader `
    --distpath $OutputDir `
    --workpath $WorkDir `
    --specpath $SpecDir `
    --add-data "$ScriptDir\config\game_config.json;config" `
    "$ScriptDir\cli.py"

if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host "打包完成：$(Join-Path $OutputDir 'cdloader.exe')" -ForegroundColor Green
