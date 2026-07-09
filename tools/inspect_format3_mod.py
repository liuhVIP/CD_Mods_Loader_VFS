"""Format 3 模组结构诊断工具。

用于快速统计真实模组的 target、intent 数量和字段分布，避免完整 apply 时才发现
某个 writer 处理量异常。
"""

from __future__ import annotations

import argparse
import json
import pprint
from collections import Counter
from pathlib import Path


def main() -> int:
    """输出 Format 3 JSON 的目标和字段统计。"""
    parser = argparse.ArgumentParser(description="统计 Format 3 模组结构")
    parser.add_argument("json_path", help="Format 3 JSON 文件路径")
    parser.add_argument("--samples", type=int, default=0, help="每个 target 打印前 N 条 intent")
    args = parser.parse_args()

    path = Path(args.json_path)
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    print(f"file={path}")
    print(f"format={data.get('format')} format_minor={data.get('format_minor')}")
    print(f"top_keys={sorted(data.keys())}")

    targets = data.get("targets")
    if isinstance(targets, list):
        target_items = targets
    else:
        target_items = [data]

    for target in target_items:
        if not isinstance(target, dict):
            continue
        target_name = target.get("target") or target.get("file")
        intents = target.get("intents") or []
        if not isinstance(intents, list):
            intents = []
        fields = Counter(
            str(intent.get("field"))
            for intent in intents
            if isinstance(intent, dict)
        )
        print(f"target={target_name} intents={len(intents)} unique_fields={len(fields)}")
        for field, count in fields.most_common(30):
            print(f"  {count} {field}")
        if args.samples > 0:
            for intent in intents[: args.samples]:
                pprint.pp(intent, width=120)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
