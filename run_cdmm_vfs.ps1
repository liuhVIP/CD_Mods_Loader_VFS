# cdmm VFS 实验启动脚本，先生成虚拟 overlay，再可选通过 vfsDmoe 注入启动游戏。
param(
    [string]$GameDir = "G:\SteamLibrary\steamapps\common\Crimson Desert",
    [string]$VfsDemoDir = "T:\C++\vfsDmoe",
    [int]$WaitSeconds = 15,
    [string]$SteamAppId = "",
    [switch]$BuildOnly,
    [switch]$NoBuildVfsDemo,
    [switch]$AllowMissingTargets,
    [switch]$KeepRunning,
    [switch]$AllowRunningTarget,
    [switch]$EnableNtOpenFileHook,
    [switch]$PatchAsiModules,
    [switch]$UseRemoteInjection
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
$PythonPath = Join-Path $ScriptDir ".venv\Scripts\python.exe"
$TargetExe = Join-Path $GameDir "bin64\CrimsonDesert.exe"
$MappingJson = Join-Path $GameDir ".cdloader\vfs_mapping_tree.json"
$VfsRunScript = Join-Path $VfsDemoDir "run_target.ps1"
$MemoryLog = Join-Path $GameDir ".cdloader\logs\vfs_exe_launch.log"
$MemorySnapshot = Join-Path $GameDir ".cdloader\vfs_launch_memory_snapshot.json"

function Find-PowerShellExecutable {
    # 优先使用 PowerShell 7；用户机器没有时自动降级到系统自带 Windows PowerShell。
    $FixedPwsh = "C:\Program Files\PowerShell\7\pwsh.exe"
    if (Test-Path -LiteralPath $FixedPwsh -PathType Leaf) {
        return $FixedPwsh
    }

    $PwshCommand = Get-Command pwsh.exe -ErrorAction SilentlyContinue
    if ($null -ne $PwshCommand) {
        return $PwshCommand.Source
    }

    $WindowsPowerShell = Get-Command powershell.exe -ErrorAction SilentlyContinue
    if ($null -ne $WindowsPowerShell) {
        return $WindowsPowerShell.Source
    }

    throw "未找到可用的 PowerShell。"
}

if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "未找到项目 Python 虚拟环境：$PythonPath"
}
if (-not (Test-Path -LiteralPath $TargetExe -PathType Leaf)) {
    throw "未找到红色沙漠主程序：$TargetExe"
}
if (-not (Test-Path -LiteralPath $VfsRunScript -PathType Leaf)) {
    throw "未找到 vfsDmoe 启动脚本：$VfsRunScript"
}

if (-not $AllowRunningTarget) {
    $targetFullPath = [System.IO.Path]::GetFullPath($TargetExe)
    $runningTargets = @(
        Get-CimInstance Win32_Process -Filter "Name = 'CrimsonDesert.exe'" |
            Where-Object {
                -not [string]::IsNullOrWhiteSpace($_.ExecutablePath) -and
                ([System.IO.Path]::GetFullPath($_.ExecutablePath) -ieq $targetFullPath)
            }
    )
    if ($runningTargets.Count -gt 0) {
        $pids = ($runningTargets | ForEach-Object { $_.ProcessId }) -join ", "
        throw "检测到 CrimsonDesert.exe 仍在运行，PID: $pids。请完全退出游戏后再重新构建 VFS；如确需覆盖运行，请追加 -AllowRunningTarget。"
    }
}

Set-Location -LiteralPath $RepoRoot
$buildArgs = @("-m", "cdmm.tools.vfs_apply", "--game-dir", $GameDir)
if ($AllowMissingTargets) {
    $buildArgs += "--allow-missing-targets"
}
& $PythonPath @buildArgs
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

if ($BuildOnly) {
    Write-Host "已完成 VFS 包构建，未启动游戏。" -ForegroundColor Yellow
    Write-Host "映射文件：$MappingJson"
    exit 0
}

# 源码入口必须与打包版复用同一套 Steam 冷启动预热判断。此前这里直接调用
# vfsDmoe，绕过了 tools/vfs_launcher.py，导致本次开机首场 VFS 偶发在 2/12 崩溃。
if (-not $UseRemoteInjection) {
    $warmupArgs = @("-m", "cdmm.tools.vfs_warmup", "--game-dir", $GameDir)
    if (-not [string]::IsNullOrWhiteSpace($SteamAppId)) {
        $warmupArgs += @("--steam-app-id", $SteamAppId)
    }
    & $PythonPath @warmupArgs
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

# Steam 预热和 VFS 构建都可能改变可用内存，因此在原生 runtime 启动前最终复查。
# 此检查只显示绿色正常或红色风险提示，绝不阻止启动。
$memoryArgs = @(
    "-m", "cdmm.tools.vfs_memory_check",
    "--phase", "pre",
    "--label", "启动前内存状态",
    "--log-path", $MemoryLog,
    "--snapshot-path", $MemorySnapshot
)
& $PythonPath @memoryArgs

# Crimson Desert 的 ASI 插件应按游戏目录原生文件加载。
# 默认清理进程级残留开关，避免 VFS runtime 二次 patch ASI 模块或强制扩大 NT Hook 面。
if ($PatchAsiModules) {
    [Environment]::SetEnvironmentVariable("VFS_DEMO_PATCH_ASI_MODULES", "1", "Process")
}
else {
    [Environment]::SetEnvironmentVariable("VFS_DEMO_PATCH_ASI_MODULES", $null, "Process")
}

if (-not $EnableNtOpenFileHook) {
    [Environment]::SetEnvironmentVariable("VFS_DEMO_ENABLE_NT_OPEN_FILE", $null, "Process")
}

$vfsArgs = @(
    "-TargetExe", $TargetExe,
    "-Configuration", "Release",
    "-VirtualRoot", $GameDir,
    "-SourceRoots", $GameDir,
    "-MappingJson", $MappingJson,
    "-WaitSeconds", "$WaitSeconds",
    "-AutoMaterializeProxyDlls:`$false"
)
if ($EnableNtOpenFileHook) {
    $vfsArgs += "-EnableNtOpenFileHook"
}
if (-not [string]::IsNullOrWhiteSpace($SteamAppId)) {
    $vfsArgs += @("-SteamAppId", $SteamAppId)
}
if ($NoBuildVfsDemo) {
    $vfsArgs += "-NoBuild"
}
if ($KeepRunning) {
    $vfsArgs += "-KeepRunning"
}
if (-not $UseRemoteInjection) {
    # Crimson Desert 默认交给现有 Ultimate ASI Loader 正常加载 runtime，避开保护初始化前远程注入。
    $vfsArgs += "-AsiLoad"
}

Set-Location -LiteralPath $VfsDemoDir
$PowerShellExe = Find-PowerShellExecutable
& $PowerShellExe -NoLogo -NoProfile -ExecutionPolicy Bypass `
    -File $VfsRunScript @vfsArgs
$vfsExitCode = $LASTEXITCODE

# run_target.ps1 的“启动验证结果”输出完成后，补充同一时刻附近的系统内存状态。
Set-Location -LiteralPath $RepoRoot
& $PythonPath -m cdmm.tools.vfs_memory_check `
    --phase post `
    --label "启动验证内存状态" `
    --log-path $MemoryLog `
    --snapshot-path $MemorySnapshot
exit $vfsExitCode
