# 批量转换游戏 mods，默认输出到游戏根目录 cdmod-converted。
[CmdletBinding()]
param(
    [string]$GameDir = 'G:\SteamLibrary\steamapps\common\Crimson Desert',
    [int]$Workers = 2
)

$ErrorActionPreference = 'Stop'
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExe = Join-Path $ProjectDir '.venv\Scripts\python.exe'
$ModsDir = Join-Path $GameDir 'mods'
$OutputDir = Join-Path $GameDir 'cdmod-converted'

if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "Project Python was not found: $PythonExe"
}

Push-Location (Split-Path -Parent $ProjectDir)
try {
    & $PythonExe -m cdmm.tools.convert_mods_to_cdmod $ModsDir $OutputDir --workers $Workers
    if ($LASTEXITCODE -ne 0) {
        throw "Bulk conversion failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
