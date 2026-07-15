"""生成 v3.1 的全局唯一坡度别名路径修正版 v3.2。

v3.1 的 PHM ``_base`` basename 与原版 UI 资源冲突，运行时被解析到额外 ``ui`` 目录，
导致 motionblending 请求路径与最终 PAMT 路径不一致。本工具保持 v3.1 的平地女性/坡面
男性资源和 phase 设计，只把八条坡度别名等长改成全局唯一 ``cd_phm_cdmmx`` 前缀，并
要求加载器索引确认这些目标不存在，确保 ``allow_new`` 按声明目录写入。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from cdmm.tools.build_female_outside_combat_author_phase_walk_mod import (
    AUTHOR_MOD_DIRECTORY_NAME,
    AUTHOR_WALK_RELATIVE_PATH,
)
from cdmm.tools.build_female_outside_combat_female_flat_male_slope_mod import (
    PHW_SAMPLE_PATHS,
    SLOPE_SAMPLE_INDEXES,
    V23_PACKAGE_NAME,
    build_mod as build_base_mod,
)

# ``basic`` 与 ``cdmmx`` 均为五字符，引用和序列化 offset 完全保持不变。
ALIAS_SOURCE_TOKEN = "cd_phw_basic_00_00_"
UNIQUE_ALIAS_TOKEN = "cd_phm_cdmmx_00_00_"

MOD_NAME = "Female Outside Combat Female Flat Unique Male Slope - Male Combat"
MOD_VERSION = "3.2-test"
MOD_ID = "cdmm.female-outside-combat-female-flat-unique-male-slope-male-combat"
OUTPUT_FILE_NAME = f"{MOD_NAME} v{MOD_VERSION}.cdmod"


def _unique_male_slope_alias_reference(phw_reference: str) -> str:
    """生成不会撞原版 basename 的等长 PHM 坡度别名。"""
    if phw_reference.count("1_pc/2_phw/") != 1:
        raise ValueError(f"PHW 坡度引用目录异常：{phw_reference}")
    if phw_reference.count(ALIAS_SOURCE_TOKEN) != 1:
        raise ValueError(f"PHW 坡度引用前缀异常：{phw_reference}")
    alias = phw_reference.replace("1_pc/2_phw/", "1_pc/1_phm/", 1).replace(
        ALIAS_SOURCE_TOKEN,
        UNIQUE_ALIAS_TOKEN,
        1,
    )
    if len(alias) != len(phw_reference):
        raise ValueError("唯一 PHM 坡度别名与 PHW 来源长度不一致")
    return alias


UNIQUE_SLOPE_ALIAS_REFERENCES = tuple(
    _unique_male_slope_alias_reference(PHW_SAMPLE_PATHS[index])
    for index in SLOPE_SAMPLE_INDEXES
)


def build_mod(
    game_dir: Path,
    baseline_path: Path,
    author_walk_path: Path,
    output_path: Path,
) -> Path:
    """生成强制使用全局唯一新增别名的 v3.2 包。"""
    return build_base_mod(
        game_dir,
        baseline_path,
        author_walk_path,
        output_path,
        slope_alias_references=UNIQUE_SLOPE_ALIAS_REFERENCES,
        mod_name=MOD_NAME,
        mod_version=MOD_VERSION,
        mod_id=MOD_ID,
        source_format="v3.1-plus-globally-unique-phm-slope-alias-paths",
        require_new_alias_targets=True,
    )


def main() -> int:
    """生成游戏 mods 目录下的 v3.2 路径修正测试包。"""
    game_dir = Path(r"G:\SteamLibrary\steamapps\common\Crimson Desert")
    mods_dir = game_dir / "mods"
    baseline_path = mods_dir / V23_PACKAGE_NAME
    author_walk_path = (
        mods_dir / AUTHOR_MOD_DIRECTORY_NAME / AUTHOR_WALK_RELATIVE_PATH
    )
    output_path = mods_dir / OUTPUT_FILE_NAME
    result = build_mod(game_dir, baseline_path, author_walk_path, output_path)
    print(f"已生成：{result}")
    print("平地：作者 PHW；坡度：唯一 PHM cdmmx 别名")
    print("新增男性坡度 PAA/metabin：16")
    print(f"v3.2 SHA-256：{hashlib.sha256(result.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
