"""Crimson Desert Steam 预热状态判定服务。

本模块统一关联游戏加载日志与 Windows WER 报告，避免一次固定偏移崩溃后
仍因本次开机较早的成功记录而永久跳过恢复性纯净启动。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from cdmm.common.constants import WORK_DIR_NAME

# 当前 Windows 会话完成 Steam 纯净预热后的标记文件。
STEAM_WARMUP_MARKER_FILE_NAME = "steam_warmup_boot.marker"
# 游戏自身运行日志相对 LocalAppData 的目录。
GAME_RUNTIME_LOG_REL_DIR = Path("Pearl Abyss") / "log"
# Windows Error Reporting 归档相对 ProgramData 的目录。
WER_REPORT_ARCHIVE_REL_DIR = Path("Microsoft") / "Windows" / "WER" / "ReportArchive"
# 固定保护层崩溃对应的应用、异常代码和模块偏移。
CRIMSON_DESERT_EXE_NAME = "CrimsonDesert.exe"
VFS_RECOVERY_EXCEPTION_CODE = "c0000005"
VFS_RECOVERY_EXCEPTION_OFFSET = "ad164d0"
# WER 通常比游戏最后一行日志晚数秒写入，允许三分钟关联窗口。
VFS_FAILURE_CORRELATION_WINDOW_SECONDS = 180
# 只检查最近的游戏日志，避免长期运行后重复读取全部历史文件。
GAME_RUNTIME_LOG_LIMIT = 50
# Windows FILETIME 到 Unix 时间戳的固定偏移和单位。
WINDOWS_FILETIME_UNIX_EPOCH = 116_444_736_000_000_000
WINDOWS_FILETIME_TICKS_PER_SECOND = 10_000_000

_GAME_LOAD_STAGE_PATTERN = re.compile(r"\[데이터\]\s+\((\d+)/12\)")


@dataclass(frozen=True)
class SteamWarmupState:
    """当前开机会话的 Steam 预热有效状态。"""

    completed: bool
    completion_time: float | None
    invalidation_time: float | None
    reason: str

    @property
    def recovery_required(self) -> bool:
        """返回是否因固定偏移崩溃而需要恢复性纯净启动。"""
        return self.invalidation_time is not None and not self.completed


@dataclass(frozen=True)
class _GameLogState:
    """单个游戏日志的加载阶段摘要。"""

    timestamp: float
    completed: bool
    stopped_at_stage_two: bool


def evaluate_steam_warmup_state(
    game_dir: Path,
    boot_time: float,
    *,
    local_app_data: Path | None = None,
    program_data: Path | None = None,
) -> SteamWarmupState:
    """按时间线判断旧预热是否被后续固定偏移崩溃作废。"""
    completion_times = _read_completion_times(game_dir, boot_time, local_app_data)
    stage_two_failures = _read_stage_two_failure_times(boot_time, local_app_data)
    wer_failures = _read_matching_wer_failure_times(boot_time, program_data)
    invalidation_time = _latest_correlated_failure(stage_two_failures, wer_failures)
    completion_time = max(completion_times, default=None)

    if invalidation_time is not None and (
        completion_time is None or completion_time <= invalidation_time
    ):
        return SteamWarmupState(
            completed=False,
            completion_time=completion_time,
            invalidation_time=invalidation_time,
            reason="检测到 VFS 在数据加载 2/12 以 0xAD164D0 崩溃，旧预热状态已失效",
        )
    if completion_time is not None:
        return SteamWarmupState(
            completed=True,
            completion_time=completion_time,
            invalidation_time=invalidation_time,
            reason="本次开机存在晚于最近固定偏移崩溃的有效预热记录",
        )
    return SteamWarmupState(
        completed=False,
        completion_time=None,
        invalidation_time=invalidation_time,
        reason="本次开机尚未完成 Steam 纯净预热",
    )


def _read_completion_times(
    game_dir: Path,
    boot_time: float,
    local_app_data: Path | None,
) -> list[float]:
    """收集当前开机的 marker 与游戏 12/12 完成时间。"""
    completion_times: list[float] = []
    marker = game_dir / WORK_DIR_NAME / STEAM_WARMUP_MARKER_FILE_NAME
    marker_time = _safe_mtime(marker)
    if marker_time is not None and marker_time >= boot_time:
        completion_times.append(marker_time)
    for state in _read_game_log_states(boot_time, local_app_data):
        if state.completed:
            completion_times.append(state.timestamp)
    return completion_times


def _read_stage_two_failure_times(
    boot_time: float,
    local_app_data: Path | None,
) -> list[float]:
    """收集当前开机停在数据加载 2/12 的游戏日志时间。"""
    return [
        state.timestamp
        for state in _read_game_log_states(boot_time, local_app_data)
        if state.stopped_at_stage_two
    ]


def _read_game_log_states(
    boot_time: float,
    local_app_data: Path | None,
) -> list[_GameLogState]:
    """读取最近游戏日志并提取完成和固定阶段停止状态。"""
    local_root = local_app_data or _environment_path("LOCALAPPDATA")
    if local_root is None:
        return []
    log_dir = local_root / GAME_RUNTIME_LOG_REL_DIR
    try:
        candidates = sorted(
            log_dir.glob("Launcher_*.log"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )[:GAME_RUNTIME_LOG_LIMIT]
    except OSError:
        return []

    states: list[_GameLogState] = []
    for log_path in candidates:
        timestamp = _safe_mtime(log_path)
        if timestamp is None or timestamp < boot_time:
            continue
        try:
            content = log_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        stages = [int(match.group(1)) for match in _GAME_LOAD_STAGE_PATTERN.finditer(content)]
        max_stage = max(stages, default=-1)
        states.append(
            _GameLogState(
                timestamp=timestamp,
                completed=max_stage >= 12,
                stopped_at_stage_two=max_stage == 2,
            )
        )
    return states


def _read_matching_wer_failure_times(
    boot_time: float,
    program_data: Path | None,
) -> list[float]:
    """读取当前开机内匹配固定异常代码和偏移的 WER 时间。"""
    program_root = program_data or _environment_path("PROGRAMDATA")
    if program_root is None:
        return []
    report_root = program_root / WER_REPORT_ARCHIVE_REL_DIR
    try:
        report_paths = [
            directory / "Report.wer"
            for directory in report_root.glob("AppCrash_CrimsonDesert*")
            if (directory / "Report.wer").is_file()
        ]
    except OSError:
        return []

    failure_times: list[float] = []
    for report_path in report_paths:
        values = _read_wer_values(report_path)
        if not _is_matching_wer_failure(values):
            continue
        event_time = _wer_event_time(values, report_path)
        if event_time is not None and event_time >= boot_time:
            failure_times.append(event_time)
    return failure_times


def _read_wer_values(report_path: Path) -> dict[str, str]:
    """兼容 UTF-16LE 与 UTF-8 的 Report.wer 键值文本。"""
    try:
        data = report_path.read_bytes()
    except OSError:
        return {}
    if data.startswith((b"\xff\xfe", b"\xfe\xff")) or b"\x00" in data[:32]:
        content = data.decode("utf-16", errors="ignore")
    else:
        content = data.decode("utf-8", errors="ignore")
    values: dict[str, str] = {}
    for line in content.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key.strip()] = value.strip()
    return values


def _is_matching_wer_failure(values: dict[str, str]) -> bool:
    """判断 WER 是否为 Crimson Desert 固定保护层偏移。"""
    app_name = values.get("NsAppName", values.get("Sig[0].Value", ""))
    exception_code = _normalize_hex(values.get("Sig[6].Value", ""))
    exception_offset = _normalize_hex(values.get("Sig[7].Value", ""))
    return (
        app_name.casefold() == CRIMSON_DESERT_EXE_NAME.casefold()
        and exception_code == VFS_RECOVERY_EXCEPTION_CODE
        and exception_offset == VFS_RECOVERY_EXCEPTION_OFFSET
    )


def _wer_event_time(values: dict[str, str], report_path: Path) -> float | None:
    """优先把 WER EventTime FILETIME 转换为 Unix 时间戳。"""
    raw_event_time = values.get("EventTime", "")
    try:
        file_time = int(raw_event_time)
        return (
            file_time - WINDOWS_FILETIME_UNIX_EPOCH
        ) / WINDOWS_FILETIME_TICKS_PER_SECOND
    except ValueError:
        return _safe_mtime(report_path)


def _latest_correlated_failure(
    stage_two_failures: list[float],
    wer_failures: list[float],
) -> float | None:
    """返回与 2/12 日志在时间窗口内对应的最新固定偏移崩溃。"""
    correlated = [
        max(game_time, wer_time)
        for game_time in stage_two_failures
        for wer_time in wer_failures
        if abs(game_time - wer_time) <= VFS_FAILURE_CORRELATION_WINDOW_SECONDS
    ]
    return max(correlated, default=None)


def _normalize_hex(value: str) -> str:
    """统一 WER 十六进制字段，去掉 0x 和高位补零。"""
    normalized = value.strip().casefold()
    if normalized.startswith("0x"):
        normalized = normalized[2:]
    return normalized.lstrip("0") or "0"


def _safe_mtime(path: Path) -> float | None:
    """读取文件时间，文件消失或权限不足时返回空。"""
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _environment_path(name: str) -> Path | None:
    """读取非空环境目录。"""
    value = os.environ.get(name, "").strip()
    return Path(value) if value else None
