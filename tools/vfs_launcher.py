"""VFS 专用单体 exe 启动入口。

本入口用于 Nuitka 打包后的 cdloader-VFS 版本化 exe。用户把 exe 放到游戏根目录后
双击运行，即可按默认 VFS 参数构建虚拟包并启动 Crimson Desert。
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import logging
import os
import re
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path
from time import perf_counter

from cdmm import cdloader_native
from cdmm.common.constants import GAME_BIN_DIR_NAME, GAME_EXECUTABLE_NAME, LOGS_DIR_NAME, WORK_DIR_NAME
from cdmm.services.vfs_loader import VfsBuildResult, build_vfs_package_for_launch
from cdmm.services.vfs_memory_service import (
    VfsMemoryStatus,
    format_gib,
    format_memory_change,
    format_memory_status,
    get_vfs_memory_status,
)
from cdmm.utils.console_alert import is_standalone_conflict, print_standalone_conflict, print_status_line

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

# VFS runtime 的核心文件，缺失时无法启动。
REQUIRED_VFS_RUNTIME_FILE_NAMES = (VFS_LAUNCHER_EXE_NAME, VFS_RUNTIME_DLL_NAME)

# 原生 launcher 依赖的 VC/UCRT 运行库，优先随包携带，避免用户机器连环缺 DLL。
VFS_RUNTIME_DEPENDENCY_DLL_NAMES = (
    "msvcp140.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
    "ucrtbase.dll",
)

# 运行时可复制的全部 VFS 文件。
VFS_RUNTIME_FILE_NAMES = REQUIRED_VFS_RUNTIME_FILE_NAMES + VFS_RUNTIME_DEPENDENCY_DLL_NAMES

# VFS runtime 日志目录名。
VFS_RUNTIME_LOG_DIR_NAME = "logs"

# 当前 Windows 开机会话完成纯净 Steam 预热后的标记文件。
STEAM_WARMUP_MARKER_FILE_NAME = "steam_warmup_boot.marker"

# 游戏自身日志目录，用于确认本次开机是否已经完整加载到 12/12。
GAME_RUNTIME_LOG_REL_DIR = Path("Pearl Abyss") / "log"

# Steam 冷启动等待游戏进程出现的最长秒数。
STEAM_WARMUP_START_TIMEOUT_SECONDS = 120

# 纯净预热过早退出时不写入成功标记，避免把启动失败误判成预热完成。
STEAM_WARMUP_MIN_RUNTIME_SECONDS = 20

# 版本文件名；打包文件名、PE 版本和运行时标题统一以此文件为来源。
VERSION_FILE_NAME = "version.txt"

# 版本文件异常时仅用于保证错误提示仍可显示。
DEFAULT_APP_VERSION = "v1.0"

# vfs_launcher 日志文件名。
VFS_NATIVE_LAUNCHER_LOG_NAME = "vfs_launcher.log"

# vfs_runtime 日志文件名。
VFS_NATIVE_RUNTIME_LOG_NAME = "vfs_runtime.log"

def bundled_resource_candidates(file_name: str) -> list[Path]:
    """返回源码和 Nuitka 单文件环境下的内置资源候选路径。"""
    candidates: list[Path] = []
    bundle_root = getattr(sys, "_MEIPASS", "")
    if bundle_root:
        candidates.extend((Path(bundle_root) / "cdmm" / file_name, Path(bundle_root) / file_name))
    package_root = Path(__file__).resolve().parents[1]
    candidates.extend((package_root / file_name, package_root.parent / file_name))
    return candidates


def app_version() -> str:
    """从唯一版本文件读取规范化版本号。"""
    for version_path in bundled_resource_candidates(VERSION_FILE_NAME):
        try:
            version = version_path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if re.fullmatch(r"v?\d+(?:\.\d+){0,3}", version, flags=re.IGNORECASE):
            return f"v{version.lstrip('vV')}"
    return DEFAULT_APP_VERSION


# 用户可见程序版本、标题和成品文件名均动态派生。
APP_VERSION = app_version()
APP_TITLE = f"红色沙漠 VFS 独立轻量模组加载器 {APP_VERSION}"
APP_EXE_NAME = f"cdloader-VFS-{APP_VERSION}.exe"

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

# Win32 进程枚举和终止常量，替代 PowerShell/WMI，降低用户机器依赖。
TH32CS_SNAPPROCESS = 0x00000002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
STILL_ACTIVE = 259
MAX_PATH_BUFFER_CHARS = 32768
MAX_PROCESS_EXE_NAME_CHARS = 260
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)


class ProcessEntry32W(ctypes.Structure):
    """Windows 进程快照结构，只保留当前启动器需要的字段。"""

    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * MAX_PROCESS_EXE_NAME_CHARS),
    ]


KERNEL32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
KERNEL32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
KERNEL32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W)]
KERNEL32.Process32FirstW.restype = wintypes.BOOL
KERNEL32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W)]
KERNEL32.Process32NextW.restype = wintypes.BOOL
KERNEL32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
KERNEL32.OpenProcess.restype = wintypes.HANDLE
KERNEL32.CloseHandle.argtypes = [wintypes.HANDLE]
KERNEL32.CloseHandle.restype = wintypes.BOOL
KERNEL32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
KERNEL32.QueryFullProcessImageNameW.restype = wintypes.BOOL
KERNEL32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
KERNEL32.GetExitCodeProcess.restype = wintypes.BOOL
KERNEL32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
KERNEL32.TerminateProcess.restype = wintypes.BOOL
KERNEL32.GetTickCount64.argtypes = []
KERNEL32.GetTickCount64.restype = ctypes.c_ulonglong


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
            result = build_vfs_package_for_launch(
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
    parser.add_argument(
        "--use-remote-injection",
        "-UseRemoteInjection",
        action="store_true",
        help="回退到启动前远程注入 runtime，仅用于兼容性排障",
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
    """解析游戏目录；兼容 exe 位于游戏根目录或其一级发布目录。"""
    if game_dir_arg is not None:
        return game_dir_arg.resolve()
    exe_dir = executable_dir()
    for candidate in (exe_dir, exe_dir.parent):
        if looks_like_game_dir(candidate):
            return candidate
    print(f"未识别到游戏根目录，请把 {APP_EXE_NAME} 放到红色沙漠游戏根目录后再运行。")
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
    """检查游戏主程序是否存在。"""
    target_exe = game_dir / GAME_BIN_DIR_NAME / GAME_EXECUTABLE_NAME
    if not target_exe.exists():
        raise FileNotFoundError(f"未找到红色沙漠主程序：{target_exe}")


def prepare_vfs_runtime(game_dir: Path, runtime_dir_arg: Path | None) -> Path:
    """把内置 VFS runtime 释放到游戏 .cdloader 目录，彻底脱离本机源码路径。"""
    source_dir = resolve_vfs_runtime_source(runtime_dir_arg)
    target_dir = game_dir / GAME_VFS_RUNTIME_REL_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / VFS_RUNTIME_LOG_DIR_NAME).mkdir(parents=True, exist_ok=True)
    for file_name in REQUIRED_VFS_RUNTIME_FILE_NAMES:
        copy_runtime_file_if_needed(source_dir / file_name, target_dir / file_name)
    for file_name in VFS_RUNTIME_DEPENDENCY_DLL_NAMES:
        dependency_source = source_dir / file_name
        if dependency_source.exists():
            copy_runtime_file_if_needed(dependency_source, target_dir / file_name)
    ensure_vfs_runtime_dependencies_available(target_dir)
    return target_dir


def resolve_vfs_runtime_source(runtime_dir_arg: Path | None) -> Path:
    """定位 VFS runtime 来源，优先命令行，其次 Nuitka 内置资产和源码私有目录。"""
    if runtime_dir_arg is not None:
        return normalize_runtime_dir(runtime_dir_arg)
    for candidate in bundled_runtime_candidates():
        if all((candidate / file_name).exists() for file_name in REQUIRED_VFS_RUNTIME_FILE_NAMES):
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


def ensure_vfs_runtime_dependencies_available(runtime_dir: Path) -> None:
    """启动原生 VFS 前预检运行库，避免弹出一连串系统缺 DLL 窗口。"""
    missing = [
        file_name
        for file_name in VFS_RUNTIME_DEPENDENCY_DLL_NAMES
        if not runtime_dependency_available(runtime_dir, file_name)
    ]
    if not missing:
        return
    missing_text = "、".join(missing)
    raise RuntimeError(
        "VFS 原生启动器当前检测到缺少运行库："
        f"{missing_text}。这些只是可预检的常见 VC/UCRT DLL，不代表全部依赖。"
        "请重新打包让运行库随 nppvfs_launcher.exe 一起释放，"
        "或安装完整 Microsoft Visual C++ 2015-2022 x64 运行库。"
    )


def runtime_dependency_available(runtime_dir: Path, file_name: str) -> bool:
    """判断 DLL 是否已经在启动目录或系统目录中可被 Windows 加载器找到。"""
    if (runtime_dir / file_name).exists():
        return True
    windows_dir = os.environ.get("WINDIR", r"C:\Windows")
    system_candidates = (
        Path(windows_dir) / "System32" / file_name,
        Path(windows_dir) / "SysWOW64" / file_name,
    )
    return any(path.exists() for path in system_candidates)


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
        if is_standalone_conflict(warning):
            logging.warning(warning)
            print_standalone_conflict(warning)
            continue
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
    steam_app_id = args.steam_app_id.strip() or resolve_steam_app_id(
        game_dir / GAME_BIN_DIR_NAME / GAME_EXECUTABLE_NAME
    )
    if not args.use_remote_injection and steam_app_id:
        ensure_steam_warmup_for_current_boot(game_dir, steam_app_id)
    elif not args.use_remote_injection:
        # 非 Steam 发行版不能伪造 AppID；保持平台无关的 ASI 直接启动路径。
        print("未识别到匹配当前游戏目录的 Steam manifest，跳过 Steam 冷启动预热。")
        logging.info("Steam 冷启动预热：非 Steam 安装或未识别到匹配 manifest，已跳过")
    prelaunch_memory_status = print_vfs_prelaunch_memory_status("启动前内存状态")
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
    print_vfs_smoke_result(
        runtime_dir,
        process.pid,
        launcher_alive,
        process.returncode,
        target_pid,
        game_pid,
        prelaunch_memory_status,
    )
    if not launcher_alive and game_pid is None:
        return int(process.returncode or 1)
    if args.no_keep_running:
        stop_processes(process.pid, game_pid)
    return 0


def ensure_steam_warmup_for_current_boot(game_dir: Path, steam_app_id: str) -> None:
    """每次 Windows 开机先完成一次无 VFS 的 Steam 原生预热。"""
    if steam_warmup_completed_for_current_boot(game_dir):
        logging.info("Steam 冷启动预热：本次开机已完成，直接进入 VFS")
        return

    target_exe = (game_dir / GAME_BIN_DIR_NAME / GAME_EXECUTABLE_NAME).resolve()
    cleanup_owned_asi_runtime_files(target_exe.parent)
    previous_pids = set(find_processes_by_exact_paths([target_exe]))
    print("")
    print("检测到本次开机尚未完成 Steam 冷启动预热。")
    print("正在先以纯净模式启动游戏；进入主菜单后请正常退出游戏。")
    print("退出后加载器会自动继续启动 VFS 模组，无需再次运行本程序。")
    logging.info("Steam 冷启动预热：请求纯净启动 AppID=%s", steam_app_id)
    os.startfile(f"steam://run/{steam_app_id}")

    started_at = time.monotonic()
    game_pid = wait_for_new_target_process(
        target_exe,
        previous_pids,
        STEAM_WARMUP_START_TIMEOUT_SECONDS,
    )
    if game_pid is None:
        raise RuntimeError("等待 Steam 纯净启动 CrimsonDesert.exe 超时")
    print(f"Steam 纯净预热进程已启动，PID：{game_pid}；等待你正常退出游戏...")
    logging.info("Steam 冷启动预热：游戏 PID=%s", game_pid)
    while is_process_running(game_pid):
        time.sleep(1)

    runtime_seconds = time.monotonic() - started_at
    if runtime_seconds < STEAM_WARMUP_MIN_RUNTIME_SECONDS:
        raise RuntimeError(
            f"Steam 纯净预热仅运行 {runtime_seconds:.1f}s，尚未完成。请重新运行并等待进入主菜单后再退出。"
        )
    write_steam_warmup_marker(game_dir)
    print("Steam 纯净预热完成，正在自动继续 VFS 启动...")
    logging.info("Steam 冷启动预热完成：%.2fs", runtime_seconds)


def wait_for_new_target_process(target_exe: Path, previous_pids: set[int], timeout_seconds: int) -> int | None:
    """等待 Steam 创建新的目标游戏进程。"""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        for pid in find_processes_by_exact_paths([target_exe]):
            if pid not in previous_pids:
                return pid
        time.sleep(0.2)
    return None


def cleanup_owned_asi_runtime_files(game_bin_dir: Path) -> None:
    """纯净预热前清理加载器自有的临时 ASI 和 sidecar。"""
    for pattern in ("nppvfs_runtime_*.asi", "nppvfs_runtime_*.asi.env"):
        for path in game_bin_dir.glob(pattern):
            try:
                path.unlink()
            except OSError as exc:
                raise RuntimeError(f"无法清理 VFS 临时文件：{path}") from exc


def current_boot_time_seconds() -> float:
    """根据 Windows 单调运行时间估算本次系统启动时间。"""
    return time.time() - (KERNEL32.GetTickCount64() / 1000.0)


def steam_warmup_completed_for_current_boot(game_dir: Path) -> bool:
    """检查本次开机是否已有加载器标记或游戏 12/12 成功日志。"""
    boot_time = current_boot_time_seconds()
    marker = game_dir / WORK_DIR_NAME / STEAM_WARMUP_MARKER_FILE_NAME
    try:
        if marker.stat().st_mtime >= boot_time:
            return True
    except OSError:
        pass

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if not local_app_data:
        return False
    log_dir = Path(local_app_data) / GAME_RUNTIME_LOG_REL_DIR
    for log_path in sorted(log_dir.glob("Launcher_*.log"), reverse=True)[:20]:
        try:
            if log_path.stat().st_mtime < boot_time:
                continue
            if "(12/12)" in log_path.read_text(encoding="utf-8", errors="ignore"):
                return True
        except OSError:
            continue
    return False


def write_steam_warmup_marker(game_dir: Path) -> None:
    """记录当前开机已完成 Steam 纯净预热。"""
    marker = game_dir / WORK_DIR_NAME / STEAM_WARMUP_MARKER_FILE_NAME
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"boot_time={current_boot_time_seconds():.3f}\n", encoding="utf-8")


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
    normalized_paths = {normalize_process_path(path) for path in paths if path.exists()}
    if not normalized_paths:
        return []
    return [
        pid
        for pid, image_path, _exe_name in iter_windows_processes()
        if image_path and normalize_process_path(image_path) in normalized_paths
    ]


def ensure_no_running_target(game_dir: Path, allow_running_target: bool) -> None:
    """默认阻止在游戏仍运行时重建并启动 VFS。"""
    if allow_running_target:
        return
    target_exe = (game_dir / GAME_BIN_DIR_NAME / GAME_EXECUTABLE_NAME).resolve()
    pids = find_processes_by_exact_paths([target_exe])
    if not pids:
        return
    pids_text = ", ".join(str(pid) for pid in pids)
    raise RuntimeError(f"检测到 CrimsonDesert.exe 仍在运行，PID: {pids_text}。请完全退出游戏后再运行。")


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
    if not args.use_remote_injection:
        # Crimson Desert 由已安装的 Ultimate ASI Loader 正常加载 runtime，避免保护初始化前远程注入。
        command.append("--asi-load")
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
    handle = open_process(PROCESS_QUERY_LIMITED_INFORMATION, pid)
    if not handle:
        return False
    try:
        exit_code = wintypes.DWORD()
        if not KERNEL32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == STILL_ACTIVE
    finally:
        KERNEL32.CloseHandle(handle)


def find_process_by_path(target_exe: Path) -> int | None:
    """按可执行文件路径查找游戏进程 PID。"""
    pids = find_processes_by_exact_paths([target_exe])
    return pids[0] if pids else None


def stop_processes(*pids: int | None) -> None:
    """按 PID 停止进程，主要用于显式 NoKeepRunning 排障场景。"""
    for pid in {pid for pid in pids if pid}:
        handle = open_process(PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION, pid)
        if not handle:
            continue
        try:
            KERNEL32.TerminateProcess(handle, 1)
        finally:
            KERNEL32.CloseHandle(handle)


def iter_windows_processes() -> list[tuple[int, str, str]]:
    """枚举 Windows 进程，返回 PID、完整路径和快照中的进程名。"""
    snapshot = KERNEL32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        return []
    processes: list[tuple[int, str, str]] = []
    try:
        entry = ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(ProcessEntry32W)
        if not KERNEL32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return processes
        while True:
            pid = int(entry.th32ProcessID)
            exe_name = entry.szExeFile
            processes.append((pid, query_process_image_path(pid), exe_name))
            if not KERNEL32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        KERNEL32.CloseHandle(snapshot)
    return processes


def query_process_image_path(pid: int) -> str:
    """读取进程完整路径；权限不足时返回空字符串。"""
    handle = open_process(PROCESS_QUERY_LIMITED_INFORMATION, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(MAX_PATH_BUFFER_CHARS)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not KERNEL32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return buffer.value
    finally:
        KERNEL32.CloseHandle(handle)


def open_process(access_mask: int, pid: int) -> wintypes.HANDLE:
    """打开进程句柄，集中处理 ctypes 类型转换。"""
    return KERNEL32.OpenProcess(access_mask, False, int(pid))


def normalize_process_path(path: str | Path) -> str:
    """规整 Windows 路径大小写和分隔符，用于进程路径精确比较。"""
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def print_vfs_smoke_result(
    runtime_dir: Path,
    launcher_pid: int,
    launcher_alive: bool,
    launcher_exit_code: int | None,
    target_pid: int | None,
    game_pid: int | None,
    prelaunch_memory_status: VfsMemoryStatus | None,
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
    print_vfs_postlaunch_memory_status("  内存状态", prelaunch_memory_status)
    print(f"  launcher日志：{logs_dir / VFS_NATIVE_LAUNCHER_LOG_NAME}")
    print(f"  runtime日志：{logs_dir / VFS_NATIVE_RUNTIME_LOG_NAME}")


def print_vfs_prelaunch_memory_status(label: str) -> VfsMemoryStatus | None:
    """启动前按固定风险线显示红绿状态，但永远不阻止游戏启动。"""
    try:
        status = get_vfs_memory_status()
    except OSError as exc:
        message = f"{label}：读取失败（{exc}），继续启动游戏。"
        print_status_line(message, success=False)
        logging.warning(message)
        return None

    summary = format_memory_status(status)
    print_status_line(f"{label}：{summary}", success=status.sufficient)
    if status.sufficient:
        print_status_line("  内存余量正常。", success=True)
    else:
        print_status_line(
            "  警告：启动前内存余量偏低，游戏可能闪退或无响应；"
            f"建议至少保留物理内存 {format_gib(status.physical_warning_threshold_bytes)}、"
            f"提交余量 {format_gib(status.commit_warning_threshold_bytes)}。"
            "请关闭 Edge、浏览器或其他高内存程序。",
            success=False,
        )
    logging.info("%s：%s；状态=%s", label.strip(), summary, "充足" if status.sufficient else "不足")
    return status


def print_vfs_postlaunch_memory_status(label: str, before: VfsMemoryStatus | None) -> None:
    """启动后只显示当前余量和变化量，不使用启动前风险线判红。"""
    try:
        status = get_vfs_memory_status()
    except OSError as exc:
        logging.warning("%s：读取失败（%s）", label.strip(), exc)
        print(f"{label}：读取失败（{exc}）")
        return

    summary = format_memory_status(status)
    print(f"{label}：{summary}")
    if before is not None:
        change = format_memory_change(before, status)
        print(f"  启动期间余量变化：{change}")
        logging.info("%s：%s；启动期间余量变化：%s", label.strip(), summary, change)
        return
    logging.info("%s：%s；没有启动前快照", label.strip(), summary)


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
