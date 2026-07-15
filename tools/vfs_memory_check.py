"""源码 VFS 启动脚本使用的内存门槛检查命令。"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from cdmm.services.vfs_memory_service import (
    VfsMemoryStatus,
    format_gib,
    format_memory_change,
    format_memory_status,
    get_vfs_memory_status,
)
from cdmm.utils.console_alert import print_status_line


def build_parser() -> argparse.ArgumentParser:
    """创建源码启动链使用的参数解析器。"""
    parser = argparse.ArgumentParser(description="检查 Crimson Desert VFS 启动内存余量")
    parser.add_argument("--label", default="启动前内存状态", help="输出状态标签")
    parser.add_argument("--log-path", type=Path, default=None, help="可选 UTF-8 日志路径")
    parser.add_argument("--phase", choices=("pre", "post"), default="pre", help="启动前检查或启动后对比")
    parser.add_argument("--snapshot-path", type=Path, default=None, help="启动前内存快照路径")
    return parser


def configure_logging(log_path: Path | None) -> None:
    """为开发入口追加内存诊断日志。"""
    if log_path is None:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
        handlers=[logging.FileHandler(log_path, mode="a", encoding="utf-8")],
        force=True,
    )


def main() -> int:
    """采样并显示 VFS 内存建议；任何结果都不阻止游戏启动。"""
    args = build_parser().parse_args()
    configure_logging(args.log_path)
    try:
        status = get_vfs_memory_status()
    except OSError as exc:
        message = f"{args.label}：读取失败（{exc}），继续启动游戏。"
        print_status_line(message, success=False)
        logging.warning("%s", message)
        return 0

    if args.phase == "post":
        print_postlaunch_status(args.label, status, args.snapshot_path)
    else:
        print_prelaunch_status(args.label, status)
        write_snapshot(args.snapshot_path, status)
    return 0


def print_prelaunch_status(label: str, status: VfsMemoryStatus) -> None:
    """按固定实测风险线输出启动前红绿状态。"""
    summary = format_memory_status(status)
    print_status_line(f"{label}：{summary}", success=status.sufficient)
    if status.sufficient:
        print_status_line("内存余量正常。", success=True)
    else:
        print_status_line(
            "警告：启动前内存余量偏低，游戏可能闪退或无响应；"
            f"建议至少保留物理内存 {format_gib(status.physical_warning_threshold_bytes)}、"
            f"提交余量 {format_gib(status.commit_warning_threshold_bytes)}。"
            "请关闭 Edge、浏览器或其他高内存程序后再启动。",
            success=False,
        )
    logging.info("%s：%s；状态=%s", label, summary, "充足" if status.sufficient else "不足")


def print_postlaunch_status(label: str, status: VfsMemoryStatus, snapshot_path: Path | None) -> None:
    """启动后只报告当前值和变化量，不再套用启动前风险线。"""
    summary = format_memory_status(status)
    print(f"{label}：{summary}")
    before = read_snapshot(snapshot_path)
    if before is not None:
        change = format_memory_change(before, status)
        print(f"启动期间余量变化：{change}")
        logging.info("%s：%s；启动期间余量变化：%s", label, summary, change)
        return
    logging.info("%s：%s；没有启动前快照", label, summary)


def write_snapshot(snapshot_path: Path | None, status: VfsMemoryStatus) -> None:
    """覆盖写入开发启动链使用的启动前内存快照。"""
    if snapshot_path is None:
        return
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "available_physical_bytes": status.available_physical_bytes,
        "available_commit_bytes": status.available_commit_bytes,
    }
    snapshot_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def read_snapshot(snapshot_path: Path | None) -> VfsMemoryStatus | None:
    """读取启动前快照；缺失或损坏时只省略变化量。"""
    if snapshot_path is None:
        return None
    try:
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
        return VfsMemoryStatus(
            total_physical_bytes=0,
            available_physical_bytes=int(payload["available_physical_bytes"]),
            total_commit_bytes=0,
            available_commit_bytes=int(payload["available_commit_bytes"]),
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
