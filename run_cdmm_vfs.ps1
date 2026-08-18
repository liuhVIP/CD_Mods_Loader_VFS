# cdmm VFS 实验启动脚本。
# 统一复用 tools/vfs_launcher.py 作为唯一 VFS 启动入口：构建虚拟 overlay 后，
# 通过内置 cdloader 专用 runtime（private/vfs_runtime）注入启动游戏，与打包版
# cdloader-VFS-vX.exe 完全同路径。
#
# 背景：此前源码入口直接调用 vfsDmoe 的通用 runtime（bin\x64\Release\vfs_runtime.dll，
# 670KB，含子进程 Hook 与 inline Hook），在游戏主菜单 RunThread 阶段稳定闪退；
# 打包版使用 cdloader 专用 runtime（vfs_runtime_crimson.dll，608KB）可正常进游戏。
# 本脚本改为统一走 tools/vfs_launcher.py 后，源码版与打包版启动行为完全一致。

param(
    [string]$GameDir = "G:\SteamLibrary\steamapps\common\Crimson Desert",
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

if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "未找到项目 Python 虚拟环境：$PythonPath"
}
if (-not (Test-Path -LiteralPath $TargetExe -PathType Leaf)) {
    throw "未找到红色沙漠主程序：$TargetExe"
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

# 与打包版 cdloader-VFS 完全相同的参数与启动路径。
$launcherArgs = @("-m", "cdmm.tools.vfs_launcher", "--game-dir", $GameDir)
if ($AllowMissingTargets) {
    $launcherArgs += "--allow-missing-targets"
}
if ($NoBuildVfsDemo) {
    $launcherArgs += "--no-build-vfs-demo"
}
if ($KeepRunning) {
    $launcherArgs += "--keep-running"
}
if ($BuildOnly) {
    $launcherArgs += "--build-only"
}
if ($AllowRunningTarget) {
    $launcherArgs += "--allow-running-target"
}
if ($EnableNtOpenFileHook) {
    $launcherArgs += "--enable-nt-open-file-hook"
}
if ($PatchAsiModules) {
    $launcherArgs += "--patch-asi-modules"
}
if ($UseRemoteInjection) {
    $launcherArgs += "--use-remote-injection"
}
if ($WaitSeconds -ne 15) {
    $launcherArgs += @("--wait-seconds", "$WaitSeconds")
}
if (-not [string]::IsNullOrWhiteSpace($SteamAppId)) {
    $launcherArgs += @("--steam-app-id", $SteamAppId)
}

Set-Location -LiteralPath $RepoRoot
& $PythonPath @launcherArgs
exit $LASTEXITCODE