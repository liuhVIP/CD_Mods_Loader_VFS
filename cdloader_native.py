"""cdloader 原生加速模块的 Python 兼容入口。

本文件只负责隔离 C++ 扩展是否存在的差异，业务代码统一导入这里。
第一阶段 C++ 只实现 hashlittle，pattern_scan 暂时保持 Python fallback。
"""

from __future__ import annotations

from typing import Any

try:
    from cdmm.native import _cdloader_native as _native
except ImportError:
    try:
        from cdmm import _cdloader_native as _native
    except ImportError:
        try:
            import _cdloader_native as _native
        except ImportError:
            _native = None

_native_hashlittle = (
    getattr(_native, "compute_hashlittle", None) if _native is not None else None
)
_native_pattern_scan = (
    getattr(_native, "pattern_scan", None) if _native is not None else None
)


def is_native_available() -> bool:
    """返回当前进程是否加载到了 C++ hashlittle 实现。"""
    return _native_hashlittle is not None


def native_status_text() -> str:
    """返回原生 hashlittle 当前加载状态，便于日志排障。"""
    if _native_hashlittle is None or _native is None:
        return "available=False"
    module_file = getattr(_native, "__file__", "<bundled>")
    return f"available=True module={module_file}"


def compute_hashlittle(
    data: bytes | bytearray | memoryview,
    initval: int = 0,
) -> int:
    """计算 Bob Jenkins hashlittle，native 不可用时自动回退到 Python。"""
    if _native_hashlittle is not None:
        return int(_native_hashlittle(data, initval)) & 0xFFFFFFFF

    from cdmm.common.hashlittle import _python_hashlittle

    return _python_hashlittle(_as_bytes(data), initval)


def pattern_scan(
    data: bytes | bytearray | memoryview,
    original_offset: int,
    original_bytes: bytes | bytearray | memoryview,
    vanilla_data: bytes | bytearray | memoryview | None = None,
    *_args: Any,
    **_kwargs: Any,
) -> int | None:
    """JSON byte patch 偏移漂移扫描，第一阶段保持 Python fallback。"""
    if _native_pattern_scan is not None:
        return _native_pattern_scan(data, original_offset, original_bytes, vanilla_data)

    from cdmm.services.json_loader import (
        NEAR_PATTERN_RELOCATION_LIMIT,
        _filter_near_pattern_candidate,
    )

    data_bytes = data if isinstance(data, (bytes, bytearray)) else bytes(data)
    original = _as_bytes(original_bytes)
    vanilla = _as_bytes(vanilla_data) if vanilla_data is not None else None

    if vanilla and original_offset < len(vanilla):
        for context_size in (24, 16, 12, 8):
            start = max(0, original_offset - context_size)
            end = min(len(vanilla), original_offset + len(original) + context_size)
            context = vanilla[start:end]
            if len(context) < context_size:
                continue
            matches = _find_all_range(data_bytes, context, 0, len(data_bytes))
            if len(matches) == 1:
                candidate = matches[0] + original_offset - start
                if candidate + len(original) <= len(data_bytes):
                    return _filter_near_pattern_candidate(
                        candidate,
                        original_offset,
                        original,
                    )

    scan_start = max(0, original_offset - 512) if len(original) < 4 else 0
    scan_end = (
        min(len(data_bytes), original_offset + 512)
        if len(original) < 4
        else len(data_bytes)
    )
    matches = _find_all_range(data_bytes, original, scan_start, scan_end)
    if len(matches) == 1:
        return _filter_near_pattern_candidate(
            matches[0],
            original_offset,
            original,
        )
    if len(original) >= 8 and matches:
        nearest = min(
            matches,
            key=lambda match: abs(match - original_offset),
        )
        if abs(nearest - original_offset) <= NEAR_PATTERN_RELOCATION_LIMIT:
            return nearest
    return None


def _as_bytes(data: bytes | bytearray | memoryview | None) -> bytes:
    """把任意 bytes-like 输入规整成 bytes，bytes 本身不复制。"""
    if data is None:
        return b""
    if isinstance(data, bytes):
        return data
    return bytes(data)


def _find_all_range(
    data: bytes | bytearray,
    pattern: bytes,
    start: int,
    end: int,
) -> list[int]:
    """在指定范围查找 pattern，避免为扫描大表复制整段 bytes。"""
    if not pattern:
        return []
    result: list[int] = []
    pos = max(start, 0)
    end = min(end, len(data))
    while True:
        index = data.find(pattern, pos, end)
        if index < 0:
            return result
        result.append(index)
        pos = index + 1
