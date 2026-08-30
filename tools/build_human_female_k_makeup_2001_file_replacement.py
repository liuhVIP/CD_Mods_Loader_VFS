"""把 2.0.01 Human Female loose 基底与 K-Makeup 合成单一 file-replacement cdmod。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from cdmm.services.cdmod_converter import (
    CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
    CDMOD_FORMAT_NAME,
    CDMOD_FORMAT_VERSION,
    CDMOD_MANIFEST_PATH,
    CDMOD_REPORT_PATH,
    _write_cdmod_zip,
)
from cdmm.services.cdmod_package import load_cdmod_package
from cdmm.tools.build_full_character_creator_witch_slots_repack import (
    DEFAULT_SLOT_MAPPINGS,
    DEFAULT_WITCH_HAIR_SLOT_MAPPINGS,
    _patch_meshparam_plaintext,
)


def _read_makeup_payloads(path: Path) -> tuple[bytes, bytes, str]:
    package = load_cdmod_package(path)
    replacements = {
        item.target.casefold(): item
        for patch in package.file_patches
        for item in patch.files
    }
    diffuse = replacements["character/texture/cd_phw_00_head_base_youth_0027.dds"].content
    normal = replacements["character/texture/cd_phw_00_head_base_youth_0027_n.dds"].content
    return diffuse, normal, hashlib.sha256(path.read_bytes()).hexdigest()


def build(source_dir: Path, makeup_package: Path, output: Path) -> None:
    source_dir = source_dir.resolve()
    makeup_package = makeup_package.resolve()
    output = output.resolve()
    diffuse, normal, makeup_sha256 = _read_makeup_payloads(makeup_package)
    documents: dict[str, object | bytes] = {}
    files: list[dict[str, object]] = []
    payloads: dict[str, bytes] = {}
    for path in sorted(source_dir.rglob("*")):
        if not path.is_file():
            continue
        parts = path.relative_to(source_dir).parts
        if len(parts) < 2 or len(parts[0]) != 4 or not parts[0].isdigit():
            continue
        target = "/".join(parts[1:])
        payload_path = f"assets/human-female/{parts[0]}/{target}"
        content = path.read_bytes()
        if path.name.casefold() in {
            "meshparam_example_damian.xml",
            "meshparam_example_kliff.xml",
        }:
            content, _face_patches, _hair_patches = _patch_meshparam_plaintext(
                content,
                DEFAULT_SLOT_MAPPINGS,
                DEFAULT_WITCH_HAIR_SLOT_MAPPINGS,
                require_equal_length=False,
            )
        payloads[payload_path] = content
        files.append(
            {
                "target": target,
                "pamt_dir": parts[0],
                "payload": payload_path,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
                "allow_new": True,
            }
        )

    makeup_targets = (
        "character/texture/cd_phw_00_head_base_youth_0027.dds",
        "character/texture/cd_phw_00_head_base_youth_0027_n.dds",
        "character/texture/cd_phw_00_head_00_0139.dds",
        "character/texture/cd_phw_00_head_00_0139_n.dds",
        "character/texture/cd_phw_00_head_00_0141.dds",
        "character/texture/cd_phw_00_head_00_0141_n.dds",
        "character/texture/cd_phw_00_head_00_0143_n.dds",
        "character/texture/cd_phw_00_head_base_youth_0019.dds",
        "character/texture/cd_phw_00_head_base_youth_0019_n.dds",
        "character/texture/cd_phw_00_head_00_0046.dds",
        "character/texture/cd_phw_00_head_00_0046_n.dds",
    )
    payloads["assets/k-makeup/diffuse.dds"] = diffuse
    payloads["assets/k-makeup/normal.dds"] = normal
    for target in makeup_targets:
        content = normal if target.casefold().endswith("_n.dds") else diffuse
        payload = "assets/k-makeup/normal.dds" if content is normal else "assets/k-makeup/diffuse.dds"
        files.append(
            {
                "target": target,
                "pamt_dir": "0009",
                "payload": payload,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
                "allow_new": True,
            }
        )

    replacement_path = "files/human-female-k-makeup-replacements.json"
    documents[replacement_path] = {"schema": 1, "files": files}
    documents.update(payloads)
    documents[CDMOD_MANIFEST_PATH] = {
        "format": CDMOD_FORMAT_NAME,
        "format_version": CDMOD_FORMAT_VERSION,
        "id": "human-female-k-makeup-2.00.01-file-replacement",
        "name": "Human Female Five Witch Faces Hairstyles K-Makeup 2.00.01",
        "version": "2.00.01",
        "author": "cdmm rebuild; K-Makeup textures by maru12259",
        "description": "2.00.01-compatible Human Female face and hairstyle slots with K-Makeup.",
        "dependencies": [],
        "source": {
            "format": "2.00.01-loose-file-replacement",
            "human_female_source": hashlib.sha256(
                b"".join(
                    path.read_bytes()
                    for path in sorted(source_dir.rglob("*"))
                    if path.is_file()
                )
            ).hexdigest(),
            "makeup_package_sha256": makeup_sha256,
        },
        "components": [
            {
                "type": CDMOD_FILE_REPLACEMENT_COMPONENT_TYPE,
                "path": replacement_path,
                "file_count": len(files),
            }
        ],
    }
    documents[CDMOD_REPORT_PATH] = {
        "schema": 1,
        "format": "2.00.01-file-replacement",
        "source_file_count": len(files) - len(makeup_targets),
        "makeup_target_count": len(makeup_targets),
        "standalone_removed": True,
        "makeup_targets": list(makeup_targets),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    _write_cdmod_zip(output, documents)
    print(json.dumps({"output": str(output), "files": len(files), "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("makeup_package", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    build(args.source_dir, args.makeup_package, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
