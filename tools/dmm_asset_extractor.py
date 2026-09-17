"""从 DMM.exe（Tauri 应用）里抽出内嵌前端资源与内嵌变更日志，供只读参考。

DMM 是 Tauri 2.x 应用：Rust 后端 + Vite/React 前端。前端资源（JS/CSS/字体/svg）
以 brotli 压缩form内嵌在 PE 的 `.rdata` 里，路径表是明文的 (ptr,len) 记录数组。
本脚本不做任何逆向推理，只做可复现的“定位 + 切分 + 解压”：

1. 解析 PE 段表，取 `.rdata` 的文件偏移与虚拟地址；
2. 在 `.rdata` 里找出所有 `/assets/` 明文字符串，取第一条，算出它的 RVA/VA；
3. 在整份文件里搜索指向该字符串的 8 字节小端指针 —— 命中的位置就是资源表
   表头；表项为 16 字节 `(ptr:u64, len:u64)`，按 `(路径, 数据)` 成对交替存放；
4. 逐对切出数据块：能 brotli 解压的就解压，否则按原样写出；
5. 顺带把内嵌 changelog（`# Changelog` 起、`### Added` 锚点区间）导出成文本。

用法：

    python tools/dmm_asset_extractor.py --exe "<DMM.exe 路径>" --out .diagnostics/dmm_assets

注意：解出的 JS 是 DMM 的作者代码，仅用于理解格式/行为，不要复制进本项目。
"""

from __future__ import annotations

import argparse
import re
import struct
from pathlib import Path

ASSET_MARKER = b"/assets/"
PRINTABLE_RUN = re.compile(rb"[\x20-\x7e\r\n]{20,}")


def section_table(raw: bytes) -> list[tuple[str, int, int, int, int]]:
    pe_offset = int.from_bytes(raw[0x3C:0x40], "little")
    section_count = int.from_bytes(raw[pe_offset + 6:pe_offset + 8], "little")
    optional_size = int.from_bytes(raw[pe_offset + 20:pe_offset + 22], "little")
    table_offset = pe_offset + 24 + optional_size
    sections = []
    for index in range(section_count):
        base = table_offset + index * 40
        name = raw[base:base + 8].rstrip(b"\0").decode("latin-1")
        virtual_size = int.from_bytes(raw[base + 8:base + 12], "little")
        virtual_address = int.from_bytes(raw[base + 12:base + 16], "little")
        raw_size = int.from_bytes(raw[base + 16:base + 20], "little")
        raw_offset = int.from_bytes(raw[base + 20:base + 24], "little")
        sections.append((name, virtual_address, virtual_size, raw_offset, raw_size))
    return sections


def find_asset_table(raw: bytes) -> tuple[int, int, int]:
    """返回 (资源表文件偏移, .rdata 文件偏移, .rdata 虚拟地址)。"""
    pe_offset = int.from_bytes(raw[0x3C:0x40], "little")
    image_base = int.from_bytes(raw[pe_offset + 24 + 24:pe_offset + 24 + 32], "little")
    sections = section_table(raw)
    rdata = next(s for s in sections if s[0] == ".rdata")
    rdata_va, rdata_offset, rdata_size = rdata[1], rdata[3], rdata[4]

    first_path = raw.find(ASSET_MARKER, rdata_offset, rdata_offset + rdata_size)
    if first_path < 0:
        raise SystemExit("在 .rdata 里找不到 /assets/ 路径字符串，可能不是 Tauri 包或版本不同")

    path_rva = rdata_va + (first_path - rdata_offset)
    pointer = struct.pack("<Q", image_base + path_rva)
    table_offset = raw.find(pointer)
    if table_offset < 0:
        raise SystemExit("找不到指向 /assets/ 路径的指针，资源表定位失败")
    return table_offset, rdata_offset, rdata_va


def carve_assets(raw: bytes, table_offset: int, rdata_offset: int, rdata_va: int) -> list[tuple[str, bytes]]:
    pe_offset = int.from_bytes(raw[0x3C:0x40], "little")
    image_base = int.from_bytes(raw[pe_offset + 48:pe_offset + 56], "little")

    def rva_to_file(rva: int) -> int | None:
        if rva < rdata_va:
            return None
        offset = rdata_offset + (rva - rdata_va)
        return offset if 0 <= offset < len(raw) else None

    records: list[tuple[int, int]] = []
    for index in range(4000):
        pointer, length = struct.unpack_from("<QQ", raw, table_offset + index * 16)
        offset = rva_to_file(pointer - image_base)
        if offset is None or not 0 < length < 0x4000000:
            break
        records.append((offset, length))

    assets: list[tuple[str, bytes]] = []
    index = 0
    while index + 1 < len(records):
        key_offset, key_length = records[index]
        data_offset, data_length = records[index + 1]
        if data_offset != key_offset + key_length:
            index += 1
            continue
        key = raw[key_offset:key_offset + key_length].rstrip(b"\0").decode("utf-8", "replace")
        assets.append((key, raw[data_offset:data_offset + data_length]))
        index += 2
    return assets


def decode(data: bytes) -> tuple[str, bytes]:
    try:
        import brotli

        return "brotli", brotli.decompress(data)
    except Exception:
        return "raw", data


def carve_changelog(raw: bytes) -> str:
    anchors = [m.start() for m in re.finditer(rb"### Added", raw)]
    if not anchors:
        return ""
    start = raw.rfind(b"# Changelog", 0, anchors[0])
    start = anchors[0] - 200 if start < 0 else start
    end = anchors[-1] + 0x8000
    return "\n".join(run.decode("latin-1") for run in PRINTABLE_RUN.findall(raw[start:end]))


def main() -> int:
    parser = argparse.ArgumentParser(description="抽取 DMM.exe 内嵌前端资源与 changelog")
    parser.add_argument("--exe", required=True, help="DMM.exe 路径")
    parser.add_argument("--out", required=True, help="资源输出目录")
    parser.add_argument("--changelog", default="", help="changelog 输出文件（可选）")
    args = parser.parse_args()

    raw = Path(args.exe).read_bytes()
    table_offset, rdata_offset, rdata_va = find_asset_table(raw)
    assets = carve_assets(raw, table_offset, rdata_offset, rdata_va)
    if not assets:
        raise SystemExit("资源表切分结果为空")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    decoded_total = 0
    for key, blob in assets:
        how, payload = decode(blob)
        name = key.strip("/").replace("/", "__")
        (out_dir / name).write_bytes(payload)
        decoded_total += len(payload)
        print("%-58s comp=%-9d %-7s dec=%d" % (key[:58], len(blob), how, len(payload)))

    if args.changelog:
        text = carve_changelog(raw)
        Path(args.changelog).write_text(text, encoding="utf-8")
        print("changelog ->", args.changelog, len(text), "chars")

    print("资源 %d 个，解出 %d 字节 -> %s" % (len(assets), decoded_total, out_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
