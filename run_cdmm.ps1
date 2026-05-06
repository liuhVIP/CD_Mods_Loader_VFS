# cdmm 统一启动脚本，直接进入 Python 主入口。
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$LoaderArgs = @()
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
$PythonPath = Join-Path $ScriptDir ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $PythonPath)) {
    Write-Host "未找到 cdmm 虚拟环境，请先安装环境。" -ForegroundColor Red
    Write-Host "uv venv cdmm\.venv --python E:\python\UV\uvpython\cpython-3.10.18-windows-x86_64-none\python.exe"
    Write-Host "uv pip install --python cdmm\.venv\Scripts\python.exe -r cdmm\requirements.txt"
    Read-Host "按 Enter 退出"
    exit 1
}

Set-Location -LiteralPath $RepoRoot
& $PythonPath -m cdmm.cli @LoaderArgs
$CdmmExitCode = $LASTEXITCODE

Read-Host "按 Enter 退出"
exit $CdmmExitCode
