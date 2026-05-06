# cdmm 统一启动脚本。
param(
    [ValidateSet("", "apply", "scan", "revert", "selfcheck")]
    [string]$Action = "",
    [string]$GameDir = ""
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
$PythonPath = Join-Path $ScriptDir ".venv\Scripts\python.exe"
$ConfigPath = Join-Path $ScriptDir "config\game_config.json"
$script:CdmmExitCode = 0

function Read-ConfiguredGameDir {
    param([string]$OverrideGameDir)

    if (-not [string]::IsNullOrWhiteSpace($OverrideGameDir)) {
        return $OverrideGameDir.Trim('"').Trim()
    }

    if (Test-Path -LiteralPath $ConfigPath) {
        try {
            $Config = Get-Content -Raw -Encoding UTF8 -LiteralPath $ConfigPath | ConvertFrom-Json
            if ($Config.game_dir) {
                $ConfiguredDir = [string]$Config.game_dir
                Write-Host "已从配置读取游戏根目录：$ConfiguredDir" -ForegroundColor Cyan
                return $ConfiguredDir.Trim('"').Trim()
            }
        } catch {
            Write-Host "配置文件读取失败，将改为手动输入：$ConfigPath" -ForegroundColor Yellow
        }
    }

    Write-Host "请输入 Crimson Desert 游戏根目录。" -ForegroundColor Cyan
    Write-Host "示例：G:\SteamLibrary\steamapps\common\Crimson Desert"
    return (Read-Host "游戏根目录").Trim('"').Trim()
}

function Confirm-GameDir {
    param([string]$TargetGameDir)

    if ([string]::IsNullOrWhiteSpace($TargetGameDir)) {
        Write-Host "未输入游戏目录，已取消。" -ForegroundColor Yellow
        return $false
    }

    $PapgtPath = Join-Path $TargetGameDir "meta\0.papgt"
    if (-not (Test-Path -LiteralPath $PapgtPath)) {
        Write-Host "目录校验失败：未找到 meta\0.papgt。" -ForegroundColor Red
        Write-Host "请确认输入的是游戏根目录，而不是 bin64、mods 或管理器目录。"
        Write-Host "当前输入：$TargetGameDir"
        return $false
    }

    return $true
}

function Invoke-LoaderCommand {
    param(
        [ValidateSet("apply", "scan", "revert")]
        [string]$LoaderCommand
    )

    $TargetGameDir = Read-ConfiguredGameDir -OverrideGameDir $GameDir
    if (-not (Confirm-GameDir -TargetGameDir $TargetGameDir)) {
        $script:CdmmExitCode = 1
        return
    }

    Set-Location $RepoRoot
    Write-Host "即将执行真实加载器命令：$LoaderCommand" -ForegroundColor Cyan
    Write-Host "游戏目录：$TargetGameDir"
    Write-Host "mods 目录：$(Join-Path $TargetGameDir 'mods')"

    & $PythonPath -m cdmm.cli $LoaderCommand --game-dir $TargetGameDir --verbose
    $script:CdmmExitCode = $LASTEXITCODE
}

function Invoke-SelfCheck {
    Set-Location $RepoRoot
    $env:PYTHONPATH = "src"

    Write-Host "开始运行 cdmm 开发自检代码检查，不会加载真实游戏目录..." -ForegroundColor Cyan
    & $PythonPath -m ruff check cdmm tests\test_cdmm_loader.py
    if ($LASTEXITCODE -ne 0) {
        $script:CdmmExitCode = $LASTEXITCODE
        return
    }

    Write-Host "开始运行 cdmm 单元测试，使用临时伪造游戏目录..." -ForegroundColor Cyan
    & $PythonPath -m pytest tests\test_cdmm_loader.py -q
    $script:CdmmExitCode = $LASTEXITCODE
}

function Read-ActionFromMenu {
    Write-Host ""
    Write-Host "请选择要执行的操作：" -ForegroundColor Cyan
    Write-Host "1. 真实加载 mods 到游戏目录"
    Write-Host "2. 只扫描 mods，不写入游戏文件"
    Write-Host "3. 恢复加载器上次写入"
    Write-Host "4. 开发自检（ruff + pytest，不加载真实游戏）"
    Write-Host "0. 退出"
    $Choice = Read-Host "请输入编号"

    switch ($Choice) {
        "1" { return "apply" }
        "2" { return "scan" }
        "3" { return "revert" }
        "4" { return "selfcheck" }
        default { return "" }
    }
}

if (-not (Test-Path -LiteralPath $PythonPath)) {
    Write-Host "未找到 cdmm 虚拟环境，请先安装环境。" -ForegroundColor Red
    Write-Host "uv venv cdmm\.venv --python E:\python\UV\uvpython\cpython-3.10.18-windows-x86_64-none\python.exe"
    Write-Host "uv pip install --python cdmm\.venv\Scripts\python.exe -r cdmm\requirements.txt"
    Read-Host "按 Enter 退出"
    exit 1
}

if ([string]::IsNullOrWhiteSpace($Action)) {
    $Action = Read-ActionFromMenu
}

switch ($Action) {
    "apply" { Invoke-LoaderCommand -LoaderCommand "apply" }
    "scan" { Invoke-LoaderCommand -LoaderCommand "scan" }
    "revert" { Invoke-LoaderCommand -LoaderCommand "revert" }
    "selfcheck" { Invoke-SelfCheck }
    default {
        Write-Host "已退出。"
        $script:CdmmExitCode = 0
    }
}

if ($script:CdmmExitCode -eq 0) {
    Write-Host "执行完成。" -ForegroundColor Green
} else {
    Write-Host "执行失败，退出码：$script:CdmmExitCode" -ForegroundColor Red
}

Read-Host "按 Enter 退出"
exit $script:CdmmExitCode
