"""VFS 专用单体 exe 启动入口。

本入口用于 Nuitka 打包后的 cdloader-VFS-v1.exe。用户把 exe 放到游戏根目录后
双击运行，即可按默认 VFS 参数构建虚拟包并启动 Crimson Desert。
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from time import perf_counter

from cdmm import cdloader_native
from cdmm.common.constants import GAME_BIN_DIR_NAME, GAME_EXECUTABLE_NAME, LOGS_DIR_NAME, WORK_DIR_NAME
from cdmm.services.vfs_loader import VfsBuildResult, build_vfs_package

# PowerShell 7 固定位置，仅用于进程检测和清理。
POWERSHELL_EXE = Path(r"C:\Program Files\PowerShell\7\pwsh.exe")

# 默认等待秒数，保持 run_cdmm_vfs.ps1 的启动行为。
DEFAULT_WAIT_SECONDS = 15

# VFS 专用日志文件名。
VFS_LAUNCH_LOG_FILE_NAME = "vfs_exe_launch.log"

# VFS runtime 在项目内的私有资产目录。
PRIVATE_VFS_RUNTIME_REL_DIR = Path("private") / "vfs_runtime"

# VFS runtime 运行时释放到游戏目录下的位置，避免依赖本机源码路径。
GAME_VFS_RUNTIME_REL_DIR = Path(WORK_DIR_NAME) / "vfs_runtime"

# VFS launcher 二进制文件名，使用 nppvfs 前缀避免和开发环境旧产物混淆。
VFS_LAUNCHER_EXE_NAME = "nppvfs_launcher.exe"

# VFS 注入 DLL 文件名。
VFS_RUNTIME_DLL_NAME = "vfs_runtime.dll"

# 必须随 exe 携带的闭源 VFS runtime 文件。
VFS_RUNTIME_FILE_NAMES = (VFS_LAUNCHER_EXE_NAME, VFS_RUNTIME_DLL_NAME)

# VFS runtime 日志目录名。
VFS_RUNTIME_LOG_DIR_NAME = "logs"

# vfs_launcher 日志文件名。
VFS_NATIVE_LAUNCHER_LOG_NAME = "vfs_launcher.log"

# vfs_runtime 日志文件名。
VFS_NATIVE_RUNTIME_LOG_NAME = "vfs_runtime.log"

# 用户可见程序标题。
APP_TITLE = "红色沙漠 VFS 独立轻量模组加载器 v1"

# 用户可见作者说明。
AUTHOR_TEXT = "作者：B站UP 改名_开发"

# 用户可见免费说明。
FREE_NOTICE_TEXT = "本工具完全免费；如果你制作视频时能提到我，我会很开心。"

# 成功启动游戏后的窗口自动关闭提示等待秒数。
AUTO_CLOSE_DELAY_SECONDS = 2.0

# Windows 子进程脱离当前控制台的启动标记，避免原生 VFS 启动器把 cmd 窗口一直占住。
DETACHED_PROCESS_FLAG = 0x00000008
CREATE_NEW_PROCESS_GROUP_FLAG = 0x00000200
DETACHED_VFS_CREATION_FLAGS = DETACHED_PROCESS_FLAG | CREATE_NEW_PROCESS_GROUP_FLAG


def main(argv: list[str] | None = None) -> int:
    """执行 VFS 构建并启动游戏。"""
    configure_console_encoding()
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.vfs_runtime_dir is None and args.vfs_demo_dir is not None:
        args.vfs_runtime_dir = args.vfs_demo_dir

    game_dir = resolve_game_dir(args.game_dir)
    if game_dir is None:
        pause_before_exit()
        return 1

    log_path = game_dir / WORK_DIR_NAME / LOGS_DIR_NAME / VFS_LAUNCH_LOG_FILE_NAME
    configure_logging(log_path)
    logging.info("cdloader native hashlittle: %s", cdloader_native.native_status_text())
    print_header()
    print(f"游戏目录：{game_dir}")
    print("默认参数：AllowMissingTargets + 自动关闭窗口")

    started = perf_counter()
    return_code = 0
    auto_close_message = ""
    try:
        ensure_game_ready(game_dir)
        runtime_dir = None
        if not args.build_only:
            runtime_dir = prepare_vfs_runtime(game_dir, args.vfs_runtime_dir)
            print(f"VFS runtime：{runtime_dir}")
        progress = VfsBuildProgressPrinter()
        progress.start()
        try:
            result = build_vfs_package(
                game_dir,
                allow_missing_targets=not args.strict_targets,
                progress_callback=progress.update,
            )
        finally:
            progress.finish()
        print_vfs_result(result)
        if result.errors:
            pause_before_exit()
            return 2
        if args.build_only:
            print("已完成 VFS 包构建，未启动游戏。")
            auto_close_message = "构建完成，窗口即将自动关闭。"
        else:
            if runtime_dir is None:
                raise RuntimeError("VFS runtime 未准备完成")
            return_code = start_game_with_vfs(game_dir, runtime_dir, args)
            if return_code == 0:
                auto_close_message = "模组加载完成，游戏已启动，窗口即将自动关闭。"
    except Exception as exc:
        logging.exception("VFS 专用启动器执行失败")
        print(f"失败：{exc}", file=sys.stderr)
        pause_before_exit()
        return 1
    finally:
        elapsed = perf_counter() - started
        logging.info("VFS 专用启动器耗时：%.2fs", elapsed)
        print(f"完成时间：{elapsed:.2f}s")

    if return_code != 0:
        print(f"游戏启动脚本返回异常退出码：{return_code}", file=sys.stderr)
        pause_before_exit()
    elif auto_close_message:
        print_auto_close_notice(auto_close_message)
    return return_code


def build_parser() -> argparse.ArgumentParser:
    """创建命令行参数解析器，兼容 PowerShell 脚本风格参数。"""
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("--game-dir", "-GameDir", type=Path, default=None, help="红色沙漠游戏根目录")
    parser.add_argument(
        "--vfs-runtime-dir",
        "-VfsRuntimeDir",
        type=Path,
        default=None,
        help="可选 VFS runtime 二进制目录；默认使用 exe 内置资产",
    )
    parser.add_argument(
        "--wait-seconds",
        "-WaitSeconds",
        type=int,
        default=DEFAULT_WAIT_SECONDS,
        help="注入启动后等待秒数",
    )
    parser.add_argument("--steam-app-id", "-SteamAppId", default="", help="可选 Steam AppId")
    parser.add_argument("--build-only", "-BuildOnly", action="store_true", help="只构建 VFS，不启动游戏")
    parser.add_argument(
        "--allow-missing-targets",
        "-AllowMissingTargets",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--no-build-vfs-demo", "-NoBuildVfsDemo", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--vfs-demo-dir", "-VfsDemoDir", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--keep-running", "-KeepRunning", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--strict-targets",
        "-StrictTargets",
        action="store_true",
        help="严格处理缺失目标；默认等价 AllowMissingTargets",
    )
    parser.add_argument(
        "--build-vfs-demo",
        "-BuildVfsDemo",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--no-keep-running",
        "-NoKeepRunning",
        action="store_true",
        help="启动验证后停止 VFS 进程，仅排障时使用；普通运行会启动游戏后自动关闭窗口",
    )
    parser.add_argument(
        "--allow-running-target",
        "-AllowRunningTarget",
        action="store_true",
        help="允许检测到游戏进程仍在运行时继续重建 VFS",
    )
    parser.add_argument(
        "--enable-nt-open-file-hook",
        "-EnableNtOpenFileHook",
        action="store_true",
        help="启用 NT OpenFile Hook，仅排障时使用",
    )
    parser.add_argument(
        "--patch-asi-modules",
        "-PatchAsiModules",
        action="store_true",
        help="允许 VFS runtime patch ASI 模块，仅复现旧行为时使用",
    )
    return parser


def configure_console_encoding() -> None:
    """尽量使用 UTF-8 控制台输出，避免中文乱码。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def configure_logging(log_path: Path) -> None:
    """配置 VFS 专用文件日志。"""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
        handlers=[logging.FileHandler(log_path, mode="w", encoding="utf-8")],
        force=True,
    )


def print_header() -> None:
    """输出启动器标题。"""
    print("")
    print("=" * 72)
    print(APP_TITLE.center(60))
    print(AUTHOR_TEXT.center(58))
    print(FREE_NOTICE_TEXT.center(48))
    print("=" * 72)


def resolve_game_dir(game_dir_arg: Path | None) -> Path | None:
    """解析游戏目录；打包 exe 默认使用自身所在目录。"""
    if game_dir_arg is not None:
        return game_dir_arg.resolve()
    exe_dir = executable_dir()
    if looks_like_game_dir(exe_dir):
        return exe_dir
    print("未识别到游戏根目录，请把 cdloader-VFS-v1.exe 放到红色沙漠游戏根目录后再运行。")
    print(r"游戏根目录需要包含：bin64\CrimsonDesert.exe")
    print(r"示例：G:\SteamLibrary\steamapps\common\Crimson Desert")
    return None


def executable_dir() -> Path:
    """获取 exe 所在目录，兼容源码运行和 Nuitka 单体 exe。"""
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return Path(sys.argv[0]).resolve().parent
    return Path.cwd().resolve()


def looks_like_game_dir(path: Path) -> bool:
    """判断目录是否像 Crimson Desert 游戏根目录。"""
    return (path / GAME_BIN_DIR_NAME / GAME_EXECUTABLE_NAME).exists()


def ensure_game_ready(game_dir: Path) -> None:
    """检查游戏主程序和 PowerShell 运行环境是否存在。"""
    target_exe = game_dir / GAME_BIN_DIR_NAME / GAME_EXECUTABLE_NAME
    if not target_exe.exists():
        raise FileNotFoundError(f"未找到红色沙漠主程序：{target_exe}")
    if not POWERSHELL_EXE.exists():
        raise FileNotFoundError(f"未找到 PowerShell 7：{POWERSHELL_EXE}")


def prepare_vfs_runtime(game_dir: Path, runtime_dir_arg: Path | None) -> Path:
    """把内置 VFS runtime 释放到游戏 .cdloader 目录，彻底脱离本机源码路径。"""
    source_dir = resolve_vfs_runtime_source(runtime_dir_arg)
    target_dir = game_dir / GAME_VFS_RUNTIME_REL_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / VFS_RUNTIME_LOG_DIR_NAME).mkdir(parents=True, exist_ok=True)
    for file_name in VFS_RUNTIME_FILE_NAMES:
        copy_runtime_file_if_needed(source_dir / file_name, target_dir / file_name)
    return target_dir


def resolve_vfs_runtime_source(runtime_dir_arg: Path | None) -> Path:
    """定位 VFS runtime 来源，优先命令行，其次 Nuitka 内置资产和源码私有目录。"""
    if runtime_dir_arg is not None:
        return normalize_runtime_dir(runtime_dir_arg)
    for candidate in bundled_runtime_candidates():
        if all((candidate / file_name).exists() for file_name in VFS_RUNTIME_FILE_NAMES):
            return candidate
    candidates_text = "\n".join(str(path) for path in bundled_runtime_candidates())
    raise FileNotFoundError(f"未找到内置 VFS runtime 二进制，请检查打包资产：\n{candidates_text}")


def normalize_runtime_dir(runtime_dir: Path) -> Path:
    """兼容旧的 vfsDmoe 工程目录参数，并规整成真正的二进制目录。"""
    resolved = runtime_dir.resolve()
    legacy_bin_dir = resolved / "bin" / "x64" / "Debug"
    if (legacy_bin_dir / VFS_LAUNCHER_EXE_NAME).exists():
        return legacy_bin_dir
    return resolved


def bundled_runtime_candidates() -> list[Path]:
    """返回可能包含内置 VFS runtime 的目录列表。"""
    candidates: list[Path] = []
    bundle_root = getattr(sys, "_MEIPASS", "")
    if bundle_root:
        candidates.extend(runtime_dirs_from_base(Path(bundle_root)))
    candidates.extend(runtime_dirs_from_base(Path(__file__).resolve().parents[1]))
    candidates.extend(runtime_dirs_from_base(Path(sys.argv[0]).resolve().parent))
    candidates.extend(runtime_dirs_from_base(Path.cwd().resolve()))
    return dedupe_paths(candidates)


def runtime_dirs_from_base(base_dir: Path) -> list[Path]:
    """基于一个根目录推导 VFS runtime 资产位置。"""
    return [
        base_dir / PRIVATE_VFS_RUNTIME_REL_DIR,
        base_dir / "cdmm" / PRIVATE_VFS_RUNTIME_REL_DIR,
    ]


def dedupe_paths(paths: list[Path]) -> list[Path]:
    """按绝对路径去重，保持原有优先级。"""
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        normalized = str(path.resolve()) if path.exists() else str(path)
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def copy_runtime_file_if_needed(source: Path, target: Path) -> None:
    """必要时复制闭源 runtime 文件，避免每次启动重复写入。"""
    if not source.exists():
        raise FileNotFoundError(f"缺少 VFS runtime 文件：{source}")
    if target.exists() and file_sha256(source) == file_sha256(target):
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())


def file_sha256(path: Path) -> str:
    """计算文件 SHA256，用于判断 runtime 是否需要更新。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def print_vfs_result(result: VfsBuildResult) -> None:
    """输出 VFS 构建摘要。"""
    for warning in result.warnings:
        logging.warning(warning)
    for error in result.errors:
        logging.error(error)
        print(f"ERROR: {error}", file=sys.stderr)
    print(f"VFS 构建完成：映射文件 {result.mapping_path}")
    print(f"VFS 输出目录：{result.vfs_root}")
    print(f"已映射文件数：{len(result.mapped_files)}")
    if result.overlay_dir:
        print(f"虚拟 overlay 包：{result.overlay_dir}")


class VfsBuildProgressPrinter:
    """VFS 构建控制台进度提示，避免大模组构建时看起来像卡住。"""

    def __init__(self, interval_seconds: float = 3.0) -> None:
        """初始化阶段状态和后台提示线程。"""
        self.interval_seconds = interval_seconds
        self.started_at = perf_counter()
        self.current_phase = "等待开始"
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """启动后台提示线程。"""
        print("开始构建 VFS 包，过程可能需要几十秒，请勿关闭窗口。", flush=True)
        self._thread = threading.Thread(target=self._run, name="vfs-build-progress", daemon=True)
        self._thread.start()

    def update(self, phase_name: str) -> None:
        """切换当前阶段并立即输出。"""
        with self._lock:
            self.current_phase = phase_name
        print(f"[{self.elapsed_text()}] {phase_name}", flush=True)

    def finish(self) -> None:
        """停止后台提示线程。"""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        print(f"[{self.elapsed_text()}] VFS 构建阶段结束", flush=True)

    def _run(self) -> None:
        """周期性输出当前阶段，给用户明确的未卡死反馈。"""
        while not self._stop_event.wait(self.interval_seconds):
            with self._lock:
                phase_name = self.current_phase
            print(f"[{self.elapsed_text()}] 仍在处理：{phase_name}", flush=True)

    def elapsed_text(self) -> str:
        """返回构建已耗时文本。"""
        return f"{perf_counter() - self.started_at:6.1f}s"


def start_game_with_vfs(game_dir: Path, runtime_dir: Path, args: argparse.Namespace) -> int:
    """通过项目内置 VFS runtime 启动 Crimson Desert。"""
    ensure_no_running_target(game_dir, args.allow_running_target)
    cleanup_stale_helper_processes(game_dir, runtime_dir)
    configure_vfs_environment(args)
    clear_native_runtime_logs(runtime_dir)
    command = build_vfs_command(game_dir, runtime_dir, args)
    logging.info("启动 VFS runtime：%s", subprocess.list2cmdline(command))
    print("正在通过 VFS 启动游戏...")
    process = subprocess.Popen(
        command,
        cwd=runtime_dir,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=DETACHED_VFS_CREATION_FLAGS,
    )
    time.sleep(max(args.wait_seconds, 0))
    process.poll()
    target_pid = read_target_pid(runtime_dir / VFS_RUNTIME_LOG_DIR_NAME / VFS_NATIVE_LAUNCHER_LOG_NAME)
    game_pid = target_pid if target_pid and is_process_running(target_pid) else find_process_by_path(
        game_dir / GAME_BIN_DIR_NAME / GAME_EXECUTABLE_NAME
    )
    launcher_alive = process.poll() is None
    print_vfs_smoke_result(runtime_dir, process.pid, launcher_alive, process.returncode, target_pid, game_pid)
    if not launcher_alive and game_pid is None:
        return int(process.returncode or 1)
    if args.no_keep_running:
        stop_processes(process.pid, game_pid)
    return 0


def cleanup_stale_helper_processes(game_dir: Path, runtime_dir: Path) -> None:
    """启动前直接结束上一轮残留辅助进程，失败时提示用户手动处理。"""
    helper_paths = [
        runtime_dir / VFS_LAUNCHER_EXE_NAME,
        game_dir / GAME_BIN_DIR_NAME / "crashpad_handler.exe",
    ]
    stale_pids = find_processes_by_exact_paths(helper_paths)
    if not stale_pids:
        return
    print(f"检测到上一轮辅助进程残留，正在结束：{', '.join(str(pid) for pid in stale_pids)}")
    stop_processes(*stale_pids)
    still_running = [pid for pid in stale_pids if is_process_running(pid)]
    if still_running:
        pids_text = ", ".join(str(pid) for pid in still_running)
        raise RuntimeError(f"无法结束上一轮辅助进程 PID: {pids_text}。请在任务管理器手动结束后再启动。")


def find_processes_by_exact_paths(paths: list[Path]) -> list[int]:
    """按完整路径查找进程 PID，避免误杀其他软件同名进程。"""
    normalized_paths = [str(path.resolve()).lower() for path in paths if path.exists()]
    if not normalized_paths:
        return []
    ps_paths = "@(" + ",".join(powershell_single_quote(path) for path in normalized_paths) + ")"
    command = [
        str(POWERSHELL_EXE),
        "-NoLogo",
        "-NoProfile",
        "-Command",
        (
            f"$targets = {ps_paths}; "
            "$items = Get-CimInstance Win32_Process | "
            "Where-Object { $_.ExecutablePath -and "
            "$targets -contains ([System.IO.Path]::GetFullPath($_.ExecutablePath).ToLowerInvariant()) }; "
            "if ($items) { ($items | ForEach-Object { $_.ProcessId }) -join ',' }"
        ),
    ]
    completed = subprocess.run(command, capture_output=True, check=False, text=True)
    return [int(part) for part in completed.stdout.strip().split(",") if part.strip().isdigit()]


def powershell_single_quote(value: str) -> str:
    """生成 PowerShell 单引号字符串。"""
    return "'" + value.replace("'", "''") + "'"


def ensure_no_running_target(game_dir: Path, allow_running_target: bool) -> None:
    """默认阻止在游戏仍运行时重建并启动 VFS。"""
    if allow_running_target:
        return
    target_exe = (game_dir / GAME_BIN_DIR_NAME / GAME_EXECUTABLE_NAME).resolve()
    command = [
        str(POWERSHELL_EXE),
        "-NoLogo",
        "-NoProfile",
        "-Command",
        (
            "$target = [System.IO.Path]::GetFullPath($args[0]); "
            "$items = @(Get-CimInstance Win32_Process -Filter \"Name = 'CrimsonDesert.exe'\" | "
            "Where-Object { $_.ExecutablePath -and "
            "([System.IO.Path]::GetFullPath($_.ExecutablePath) -ieq $target) }); "
            "if ($items.Count -gt 0) { "
            "($items | ForEach-Object { $_.ProcessId }) -join ','; exit 7 }"
        ),
        str(target_exe),
    ]
    completed = subprocess.run(command, capture_output=True, check=False, text=True)
    if completed.returncode != 7:
        return
    pids = completed.stdout.strip() or "未知"
    raise RuntimeError(f"检测到 CrimsonDesert.exe 仍在运行，PID: {pids}。请完全退出游戏后再运行。")


def configure_vfs_environment(args: argparse.Namespace) -> None:
    """配置 vfsDmoe runtime 环境变量，默认跳过 ASI patch 和 NT OpenFile Hook。"""
    if args.patch_asi_modules:
        os.environ["VFS_DEMO_PATCH_ASI_MODULES"] = "1"
    else:
        os.environ.pop("VFS_DEMO_PATCH_ASI_MODULES", None)
    if args.enable_nt_open_file_hook:
        os.environ["VFS_DEMO_ENABLE_NT_OPEN_FILE"] = "1"
    else:
        os.environ.pop("VFS_DEMO_ENABLE_NT_OPEN_FILE", None)


def clear_native_runtime_logs(runtime_dir: Path) -> None:
    """清理上一次 VFS native runtime 日志，便于定位本次启动。"""
    logs_dir = runtime_dir / VFS_RUNTIME_LOG_DIR_NAME
    logs_dir.mkdir(parents=True, exist_ok=True)
    for file_name in (VFS_NATIVE_LAUNCHER_LOG_NAME, VFS_NATIVE_RUNTIME_LOG_NAME):
        try:
            (logs_dir / file_name).unlink()
        except FileNotFoundError:
            continue


def build_vfs_command(game_dir: Path, runtime_dir: Path, args: argparse.Namespace) -> list[str]:
    """生成直接调用 nppvfs_launcher.exe 的参数列表。"""
    target_exe = game_dir / GAME_BIN_DIR_NAME / GAME_EXECUTABLE_NAME
    mapping_json = game_dir / WORK_DIR_NAME / "vfs_mapping_tree.json"
    steam_app_id = args.steam_app_id.strip() or resolve_steam_app_id(target_exe)
    command = [
        str(runtime_dir / VFS_LAUNCHER_EXE_NAME),
        "--target",
        str(target_exe),
        "--virtual-root",
        str(game_dir),
        "--source-roots",
        str(game_dir),
        "--mapping-json",
        str(mapping_json),
    ]
    if steam_app_id:
        print(f"Steam AppID：{steam_app_id}")
        command.extend(["--steam-appid", steam_app_id])
    return command


def resolve_steam_app_id(target_exe: Path) -> str:
    """从 Steam manifest 自动识别 AppID，避免依赖旧 PowerShell 启动脚本。"""
    steamapps_dir = find_steamapps_dir(target_exe)
    if steamapps_dir is None:
        return ""
    common_dir = steamapps_dir / "common"
    target_full = str(target_exe.resolve()).lower()
    for manifest in steamapps_dir.glob("appmanifest_*.acf"):
        content = manifest.read_text(encoding="utf-8", errors="ignore")
        app_id = read_steam_manifest_value(content, "appid")
        install_dir = read_steam_manifest_value(content, "installdir")
        if not app_id or not install_dir:
            continue
        app_root = str((common_dir / install_dir).resolve()).lower()
        if target_full.startswith(app_root):
            return app_id
    return ""


def find_steamapps_dir(target_exe: Path) -> Path | None:
    """从游戏路径向上反推 steamapps 目录。"""
    current = target_exe.resolve().parent
    while current.parent != current:
        if current.name.lower() == "common" and current.parent.name.lower() == "steamapps":
            return current.parent
        current = current.parent
    return None


def read_steam_manifest_value(content: str, key: str) -> str:
    """读取 Steam ACF 文本中的简单键值。"""
    match = re.search(rf'"{re.escape(key)}"\s+"([^"]*)"', content, re.IGNORECASE)
    return match.group(1) if match else ""


def read_target_pid(log_path: Path) -> int | None:
    """从 vfs_launcher.log 中读取目标游戏 PID。"""
    if not log_path.exists():
        return None
    content = log_path.read_text(encoding="utf-8", errors="ignore")
    matches = re.findall(r"\[launcher\] target pid: (\d+)", content)
    return int(matches[-1]) if matches else None


def is_process_running(pid: int) -> bool:
    """判断指定 PID 是否仍在运行。"""
    command = [
        str(POWERSHELL_EXE),
        "-NoLogo",
        "-NoProfile",
        "-Command",
        "if (Get-Process -Id $args[0] -ErrorAction SilentlyContinue) { exit 0 } exit 1",
        str(pid),
    ]
    return subprocess.run(command, check=False).returncode == 0


def find_process_by_path(target_exe: Path) -> int | None:
    """按可执行文件路径查找游戏进程 PID。"""
    command = [
        str(POWERSHELL_EXE),
        "-NoLogo",
        "-NoProfile",
        "-Command",
        (
            "$target = [System.IO.Path]::GetFullPath($args[0]); "
            "$item = Get-Process -ErrorAction SilentlyContinue | "
            "Where-Object { $_.Path -and ([System.IO.Path]::GetFullPath($_.Path) -ieq $target) } | "
            "Select-Object -First 1; "
            "if ($item) { $item.Id }"
        ),
        str(target_exe.resolve()),
    ]
    completed = subprocess.run(command, capture_output=True, check=False, text=True)
    output = completed.stdout.strip()
    return int(output) if output.isdigit() else None


def stop_processes(*pids: int | None) -> None:
    """按 PID 停止进程，主要用于显式 NoKeepRunning 排障场景。"""
    for pid in {pid for pid in pids if pid}:
        subprocess.run(
            [
                str(POWERSHELL_EXE),
                "-NoLogo",
                "-NoProfile",
                "-Command",
                "Stop-Process -Id $args[0] -Force -ErrorAction SilentlyContinue",
                str(pid),
            ],
            check=False,
        )


def print_vfs_smoke_result(
    runtime_dir: Path,
    launcher_pid: int,
    launcher_alive: bool,
    launcher_exit_code: int | None,
    target_pid: int | None,
    game_pid: int | None,
) -> None:
    """输出和旧 run_target.ps1 接近的启动验证结果。"""
    logs_dir = runtime_dir / VFS_RUNTIME_LOG_DIR_NAME
    print("启动验证结果：")
    print(f"  启动器PID：{launcher_pid}")
    print(f"  启动器仍在运行：{launcher_alive}")
    print(f"  启动器退出码：{'运行中' if launcher_alive else launcher_exit_code}")
    print(f"  目标PID(日志)：{target_pid if target_pid else '未记录'}")
    print(f"  游戏PID：{game_pid if game_pid else '未找到'}")
    print(f"  游戏仍在运行：{bool(game_pid)}")
    print(f"  launcher日志：{logs_dir / VFS_NATIVE_LAUNCHER_LOG_NAME}")
    print(f"  runtime日志：{logs_dir / VFS_NATIVE_RUNTIME_LOG_NAME}")


def pause_before_exit() -> None:
    """失败路径暂停，避免窗口一闪而过导致用户看不到错误。"""
    try:
        input("按 Enter 退出")
    except EOFError:
        pass


def print_auto_close_notice(message: str) -> None:
    """成功路径短暂显示结果后自动关闭窗口。"""
    print(message, flush=True)
    time.sleep(AUTO_CLOSE_DELAY_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
