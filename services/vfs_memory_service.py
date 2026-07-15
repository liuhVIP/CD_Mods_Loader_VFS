"""VFS 启动前后的 Windows 内存状态采样与风险提示门槛。

实测表明 Crimson Desert 自身启动通常消耗约 6 GiB 物理内存和 10-11 GiB
提交量。本模块只负责采样和判断启动前风险，不阻止启动，也不结束用户进程。
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from ctypes import wintypes

# 字节换算常量，用于统一用户输出和风险门槛。
GIB = 1024**3

# 启动前物理内存风险线：实测基础消耗约 6 GiB，额外保留约 2 GiB 波动空间。
PHYSICAL_WARNING_THRESHOLD_BYTES = 8 * GIB

# 启动前 Windows 提交余量风险线：覆盖实测 10-11 GiB 的基础启动增长。
COMMIT_WARNING_THRESHOLD_BYTES = 12 * GIB


class MemoryStatusEx(ctypes.Structure):
    """GlobalMemoryStatusEx 使用的 Win32 结构。"""

    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


@dataclass(frozen=True)
class VfsMemoryStatus:
    """一次 VFS 启动内存采样及其固定风险提示门槛。"""

    total_physical_bytes: int
    available_physical_bytes: int
    total_commit_bytes: int
    available_commit_bytes: int
    physical_warning_threshold_bytes: int = PHYSICAL_WARNING_THRESHOLD_BYTES
    commit_warning_threshold_bytes: int = COMMIT_WARNING_THRESHOLD_BYTES

    @property
    def physical_sufficient(self) -> bool:
        """可用物理内存是否高于启动风险线。"""
        return self.available_physical_bytes >= self.physical_warning_threshold_bytes

    @property
    def commit_sufficient(self) -> bool:
        """系统提交余量是否高于启动风险线。"""
        return self.available_commit_bytes >= self.commit_warning_threshold_bytes

    @property
    def sufficient(self) -> bool:
        """两项关键内存指标是否都足够。"""
        return self.physical_sufficient and self.commit_sufficient


def get_vfs_memory_status() -> VfsMemoryStatus:
    """通过 GlobalMemoryStatusEx 读取物理内存和 Windows 提交余量。"""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    global_memory_status_ex = kernel32.GlobalMemoryStatusEx
    global_memory_status_ex.argtypes = [ctypes.POINTER(MemoryStatusEx)]
    global_memory_status_ex.restype = wintypes.BOOL

    native_status = MemoryStatusEx()
    native_status.dwLength = ctypes.sizeof(native_status)
    if not global_memory_status_ex(ctypes.byref(native_status)):
        error_code = ctypes.get_last_error()
        raise OSError(error_code, "GlobalMemoryStatusEx 读取系统内存状态失败")

    return VfsMemoryStatus(
        total_physical_bytes=int(native_status.ullTotalPhys),
        available_physical_bytes=int(native_status.ullAvailPhys),
        total_commit_bytes=int(native_status.ullTotalPageFile),
        available_commit_bytes=int(native_status.ullAvailPageFile),
    )


def format_memory_status(status: VfsMemoryStatus) -> str:
    """生成不包含推测性“需要值”的单行内存摘要。"""
    return (
        f"可用物理内存 {format_gib(status.available_physical_bytes)}"
        f"；提交余量 {format_gib(status.available_commit_bytes)}"
    )


def format_memory_change(before: VfsMemoryStatus, current: VfsMemoryStatus) -> str:
    """描述启动期间系统可用物理内存和提交余量的变化。"""
    physical_change = _format_available_change(
        before.available_physical_bytes,
        current.available_physical_bytes,
    )
    commit_change = _format_available_change(
        before.available_commit_bytes,
        current.available_commit_bytes,
    )
    return f"可用物理内存{physical_change}；提交余量{commit_change}"


def _format_available_change(before_bytes: int, current_bytes: int) -> str:
    """把可用量变化格式化为“减少”或“增加”。"""
    delta = before_bytes - current_bytes
    direction = "减少" if delta >= 0 else "增加"
    return f"{direction} {format_gib(abs(delta))}"


def format_gib(value: int) -> str:
    """把字节数格式化为 GiB。"""
    return f"{value / GIB:.2f} GiB"
