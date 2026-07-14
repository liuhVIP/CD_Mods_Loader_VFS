"""控制台醒目告警输出。

本模块只负责显示，不参与冲突判定和构建决策，避免控制台样式与加载逻辑耦合。
"""

from __future__ import annotations

import os
import sys
from typing import TextIO

from cdmm.services.standalone_archive_service import STANDALONE_CONFLICT_WARNING_PREFIX


# CMD 告警块宽度，固定 ASCII 边框在中英文 Windows 控制台中都易于扫描。
CONSOLE_ALERT_WIDTH = 78
# ANSI 亮红色与复位序列；现代 Windows Terminal、PowerShell 7 和 CMD 均支持。
ANSI_BRIGHT_RED = "\x1b[91m"
ANSI_RESET = "\x1b[0m"


def is_standalone_conflict(message: str) -> bool:
    """判断消息是否为 standalone PAMT/PAZ 冲突。"""
    return message.startswith(STANDALONE_CONFLICT_WARNING_PREFIX)


def format_standalone_conflict(message: str) -> str:
    """生成便于 CMD 阅读的固定分段冲突块。"""
    details = message.removeprefix(STANDALONE_CONFLICT_WARNING_PREFIX).strip()
    border = "=" * CONSOLE_ALERT_WIDTH
    separator = "-" * CONSOLE_ALERT_WIDTH
    return "\n".join(
        (
            border,
            "[!] STANDALONE ARCHIVE CONFLICT / 独立归档资源冲突",
            separator,
            details,
            separator,
            "[!] 已保留全部模组并继续启动；若游戏异常，请禁用其中一个冲突模组。",
            border,
        )
    )


def print_standalone_conflict(message: str, stream: TextIO | None = None) -> None:
    """以亮红色输出冲突块；重定向到文件时不写入 ANSI 控制字符。"""
    output = stream or sys.stderr
    block = format_standalone_conflict(message)
    if _supports_color(output):
        print(f"{ANSI_BRIGHT_RED}{block}{ANSI_RESET}", file=output, flush=True)
        return
    print(block, file=output, flush=True)


def _supports_color(stream: TextIO) -> bool:
    """仅对真实交互控制台启用颜色，保证日志文件保持纯文本。"""
    return not os.environ.get("NO_COLOR") and bool(getattr(stream, "isatty", lambda: False)())
