"""Format 3 执行期上下文与结果模型。

这里不直接处理具体表的二进制写入，只负责承载运行期上下文、writer 返回值
以及跳过原因汇总，给 `format3_loader.py` 和后续新增的 table writer 复用。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from cdmm.services.format3_parser import Format3Intent


@dataclass(frozen=True)
class Format3RuntimeContext:
    """Format 3 writer 执行时需要的只读上下文。"""

    game_file: str
    table_name: str
    body: bytes
    header: bytes
    key_size: int
    entry_bounds: dict[int, tuple[int, int, str, int]]


@dataclass(frozen=True)
class Format3SkippedIntent:
    """被跳过的 intent 及原因。"""

    intent: Format3Intent
    reason: str


@dataclass(frozen=True)
class Format3DispatchResult:
    """单个 writer 的执行结果。"""

    changes: tuple[dict, ...] = ()
    skipped: tuple[Format3SkippedIntent, ...] = ()

    @property
    def skipped_count(self) -> int:
        """返回跳过的 intent 数量。"""
        return len(self.skipped)

    @property
    def change_count(self) -> int:
        """返回生成的 byte patch 数量。"""
        return len(self.changes)

    def as_legacy_tuple(self) -> tuple[list[dict], int]:
        """兼容旧桥接层 `(changes, skipped_count)` 风格。"""
        return list(self.changes), self.skipped_count


def summarize_skip_reasons(
    skipped: tuple[Format3SkippedIntent, ...],
    *,
    max_reasons: int = 3,
) -> str:
    """按原因聚合跳过摘要，避免 warning 过长。"""
    if not skipped:
        return ""
    reason_counts = Counter(item.reason for item in skipped)
    parts: list[str] = []
    for index, (reason, count) in enumerate(reason_counts.most_common()):
        if index >= max_reasons:
            break
        parts.append(f"{count}x {reason}")
    remaining = len(reason_counts) - min(len(reason_counts), max_reasons)
    if remaining > 0:
        parts.append(f"其余 {remaining} 类原因")
    return "；".join(parts)
