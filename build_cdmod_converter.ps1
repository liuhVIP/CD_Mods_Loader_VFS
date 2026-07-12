# `.cdmod` 模组作者控制台转换器 Nuitka 打包脚本。
[CmdletBinding()]
param(
    [string]$PythonPath = "",
    [string]$DistDir = "dist_nuitka"
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# 所有发布产物从 version.txt 读取同一个版本号。
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VersionFile = Join-Path $ScriptDir "version.txt"
$AppVersion = (Get-Content -LiteralPath $VersionFile -Raw -Encoding UTF8).Trim()
if ($AppVersion -notmatch '^v?\d+(\.\d+){0,3}$') {
    throw "version.txt 格式无效：$AppVersion"
}
$VersionNumbers = @($AppVersion.TrimStart('v', 'V').Split('.') | ForEach-Object { [int]$_ })
while ($VersionNumbers.Count -lt 4) { $VersionNumbers += 0 }
$PeVersion = $VersionNumbers -join '.'
$DisplayVersion = "v$($AppVersion.TrimStart('v', 'V'))"
$OutputName = "cdmod-converter-$DisplayVersion"

if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    $PythonPath = Join-Path $ScriptDir ".venv-nuitka\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "未找到 Nuitka Python：$PythonPath"
}

$OutputDir = Join-Path $ScriptDir $DistDir
$BuildDir = Join-Path $ScriptDir "build\nuitka-cdmod-converter"
$EntryFile = Join-Path $BuildDir "cdmod_converter_entry.py"
$PackageDir = Join-Path $BuildDir "package"
$ZipPath = Join-Path $OutputDir "$OutputName.zip"

if (Test-Path -LiteralPath $BuildDir) {
    Remove-Item -LiteralPath $BuildDir -Recurse -Force
}
New-Item -ItemType Directory -Path $BuildDir,$PackageDir,$OutputDir -Force | Out-Null

@"
from cdmm.tools.cdmod_converter_cli import main

raise SystemExit(main())
"@ | Set-Content -LiteralPath $EntryFile -Encoding UTF8

$ParentDir = Split-Path -Parent $ScriptDir
Push-Location $ParentDir
try {
    $NuitkaArgs = @(
        "-m", "nuitka",
        "--standalone",
        "--assume-yes-for-downloads",
        "--output-filename=$OutputName.exe",
        "--output-dir=$(Join-Path $BuildDir 'output')",
        "--remove-output",
        "--windows-console-mode=force",
        "--company-name=cdmm",
        "--product-name=Crimson Desert cdmod Converter",
        "--file-description=Crimson Desert cdmod Converter",
        "--file-version=$PeVersion",
        "--product-version=$PeVersion",
        "--nofollow-import-to=pytest",
        "--nofollow-import-to=ruff",
        "--nofollow-import-to=tkinter",
        "--include-module=cdmm.native._cdloader_native",
        "--include-package=cryptography",
        "--include-package=lz4",
        "--include-data-file=$VersionFile=cdmm/version.txt",
        "--jobs=$([Environment]::ProcessorCount)",
        $EntryFile
    )
    & $PythonPath @NuitkaArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    Pop-Location
    Remove-Item -LiteralPath $EntryFile -Force -ErrorAction SilentlyContinue
}

$NuitkaDistDir = Get-ChildItem -LiteralPath (Join-Path $BuildDir "output") -Directory -Filter "*.dist" | Select-Object -First 1
if ($null -eq $NuitkaDistDir) {
    throw "Nuitka 已结束，但未找到转换器输出目录"
}
Copy-Item -Path (Join-Path $NuitkaDistDir.FullName "*") -Destination $PackageDir -Recurse

# 发布包内附简明使用说明，不包含内部开发清单。
@"
Crimson Desert .cdmod Converter $DisplayVersion

1. Extract the complete ZIP.
2. Run $OutputName.exe.
3. Select Chinese or English on first launch, then follow the console prompts.
4. Use the matching $DisplayVersion cdloader to load generated .cdmod files.
"@ | Set-Content -LiteralPath (Join-Path $PackageDir "README.txt") -Encoding UTF8

if (Test-Path -LiteralPath $ZipPath) {
    Remove-Item -LiteralPath $ZipPath -Force
}
Compress-Archive -Path (Join-Path $PackageDir "*") -DestinationPath $ZipPath -CompressionLevel Optimal
$Hash = (Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Host "转换器打包完成：$ZipPath" -ForegroundColor Green
Write-Host "SHA256：$Hash" -ForegroundColor Green
