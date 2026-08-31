"""为 Trinity 动态物品目录生成简体中文运行时回退表。

数据只来自当前受支持游戏版本的原版 ``iteminfo``、``ItemGroupInfo`` 与
``localizationstring_zho-cn.paloc``。生成结果编译进 ``TrinityCN.asi``，
发布目录不携带游戏原始表或中间 JSON。
"""

from __future__ import annotations

import argparse
import hashlib
import struct
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT.parent))

from cdmm.archive.pamt import parse_pamt_filtered  # noqa: E402
from cdmm.services.json_loader import extract_plaintext  # noqa: E402
from cdmm.services.pab_table_service import parse_pabgh_index  # noqa: E402
from cdmm.services.paloc import parse_paloc  # noqa: E402

# Trinity V1.3.2 VTweak 所对应的当前 Crimson Desert 游戏主程序哈希。
EXPECTED_GAME_EXE_SHA256 = (
    "B596A498701DFCDC49C486D890C42755DABC8C174314C7F26F7329394452446D"
)

# 动态目录生成依赖的原版明文表哈希；任一漂移都必须重新分析后再更新。
EXPECTED_ASSET_SHA256 = {
    "iteminfo.pabgb": "51F87FB41046C1D8DE9F84DE6F11E51BA2A837205F121FA5825552C2E6948746",
    "iteminfo.pabgh": "2621A26D3432C02DE4692361EBA6F437B7B16D2233A6131EAD280265FC52D627",
    "itemgroupinfo.pabgb": "115A27E43E5DEDD8930B967240AB2BC61FFCB7A5BFBBD617F9A9D38CB8D29C41",
    "itemgroupinfo.pabgh": "748966C64D9A582B77F2B6F60ABB0A082A9EFB72CDCEC8071B7E0B4A2C3DAB77",
    "inventory.pabgb": "EE841F4BA922538B32ECCB9665F269861099502C66384CE1745503BBCE70AAA6",
    "inventory.pabgh": "9FAB547265374C3052BE71F1C1CF2DA80AF641E338F0AB80CFE0778F45B70D32",
    "localizationstring_zho-cn.paloc": (
        "B8F209C4AF224E4BCF103961BA72EDB8E8722DCAA8B9D04A9CB234874EFC04DF"
    ),
}

# 当前游戏表的严格行数与可用中文记录数，用于拒绝部分解析或错误分包。
EXPECTED_ITEM_ROWS = 6810
EXPECTED_ITEM_OFFICIAL_TRANSLATIONS = 6738
EXPECTED_ITEM_MOD_FALLBACKS = 72
EXPECTED_GROUP_ROWS = 1597
EXPECTED_GROUP_TRANSLATIONS = 1594
EXPECTED_INVENTORY_ROWS = 21
EXPECTED_INVENTORY_TRANSLATIONS = 21

# InventoryInfo 的官方名称存在多条“背包/仓库”，平铺显示时按稳定引擎 key 中文消歧。
INVENTORY_NAME_OVERRIDES = {
    "Money": "营地",
    "Character": "背包",
    "PearlUser": "账号珍珠背包",
    "PearlCharacter": "角色珍珠背包",
    "Quest": "任务物品",
    "CampWareHouse": "营地仓库",
    "InvisibleInventory": "隐藏背包",
    "Housing_Symbol": "家园象征背包",
}

# inventory.pabgb 每行 _InventoryNameUIText 使用的稳定本地化字段编号。
INVENTORY_NAME_FIELD_ID = 0x680

# 当前 ItemInfo 中没有官方 PALOC 名称的开发、测试与特殊物品。
# 保留 [mod] 风险标记，但显示名必须人工翻译为中文，禁止回退到英文内部 key。
ITEM_MOD_NAME_OVERRIDES = {
    "Specialty_Cigar_TwoHandAxe": "特制雪茄双手斧",
    "LightSaber_TwoHandSword": "光剑",
    "Wolf_Test_OneHandSword": "狼族测试单手剑",
    "TestTwoHandAxe": "测试双手斧",
    "TestShield": "测试盾牌",
    "Lance_OneHandLance": "单手骑枪",
    "Dev_Specialty_Crudell_OneHandAxe": "开发用克鲁德尔特制单手斧",
    "TestSword": "测试剑",
    "Specialty_Mercenary_OneHandSword": "佣兵特制单手剑",
    "Specialty_Dragon_Slayer_OneHandSword": "屠龙者特制单手剑",
    "Test_OneHandAxe": "测试单手斧",
    "Test_OneHandMace": "测试单手钝器",
    "MastKey_Gloves": "万能钥匙手套",
    "Dev_Growth_Ring_I": "开发用成长戒指 I",
    "Dev_Growth_Ring_II": "开发用成长戒指 II",
    "Dev_Growth_Ring_III": "开发用成长戒指 III",
    "Dev_Growth_Ring_IV": "开发用成长戒指 IV",
    "Dev_Growth_Ring_V": "开发用成长戒指 V",
    "Dev_Growth_Ring_VI": "开发用成长戒指 VI",
    "Dev_Growth_Ring_VII": "开发用成长戒指 VII",
    "Dev_Growth_Ring_VIII": "开发用成长戒指 VIII",
    "Dev_Growth_Ring_IX": "开发用成长戒指 IX",
    "Dev_Growth_Ring_X": "开发用成长戒指 X",
    "Dev_Speed_Ring": "开发用速度戒指",
    "Dev_Red_Dragon_Ring_Str_HP": "开发用红龙力量生命戒指",
    "Dev_Red_Dragon_Ring_Def_HP": "开发用红龙防御生命戒指",
    "Dev_Red_Dragon_Ring_MP_Stamina": "开发用红龙精神耐力戒指",
    "Dev_Red_Dragon_Ring_Immune": "开发用红龙免疫戒指",
    "TestNeck_1_1": "测试项链 1-1",
    "TestNeck_1_2": "测试项链 1-2",
    "TestNeck_2_1": "测试项链 2-1",
    "TestNeck_2_2": "测试项链 2-2",
    "TestNeck_3_1": "测试项链 3-1",
    "TestNeck_3_2": "测试项链 3-2",
    "TestNeck_4_1": "测试项链 4-1",
    "TestNeck_4_2": "测试项链 4-2",
    "TestNeck_5_1": "测试项链 5-1",
    "TestNeck_5_2": "测试项链 5-2",
    "TestNeck_5_3": "测试项链 5-3",
    "Testarmor": "测试防具",
    "Douglas_Leather_Armor_T5_QA": "道格拉斯皮甲 T5 测试版",
    "Deerking_Leather_Armor_T4_QA": "鹿王皮甲 T4 测试版",
    "Tychaon_Fabric_Armor_T5_QA": "泰查恩布甲 T5 测试版",
    "Scalaphynion_Fabric_Armor_T4_QA": "斯卡拉菲尼翁布甲 T4 测试版",
    "Tychaon_Fabric_Cloak_T5_QA": "泰查恩布披风 T5 测试版",
    "Scalaphynion_Fabric_Cloak_T4_QA": "斯卡拉菲尼翁布披风 T4 测试版",
    "Heisellen_Fabric_Cloak_T1_QA": "海瑟伦布披风 T1 测试版",
    "Dev_Red_Dragon_Yann_Leather_Armor": "开发用红龙扬皮甲",
    "Dev_Red_Dragon_Oongka_Leather_Armor": "开发用红龙翁卡皮甲",
    "Dev_Red_Dragon_Kliff_Leather_Armor": "开发用红龙克里夫皮甲",
    "Demian_Fabric_Cloak_V": "德米安布披风 V",
    "Socket_Test_Gloves": "插槽测试手套",
    "Douglas_Leather_Gloves_T5_QA": "道格拉斯皮手套 T5 测试版",
    "Legendary_Deer_Leather_Gloves_T4_QA": "传说鹿皮手套 T4 测试版",
    "Reeddevil_Fabric_Gloves_T5_QA": "芦苇恶魔布手套 T5 测试版",
    "Tarif_Fabric_Gloves_T4_QA": "塔里夫布手套 T4 测试版",
    "Tarif_Fabric_Gloves_T3_QA": "塔里夫布手套 T3 测试版",
    "Hanbok_Fabric_Gloves_T1_QA": "韩服布手套 T1 测试版",
    "Restraint_Rope_Fabric_Gloves_I": "束缚绳布手套 I",
    "Bainian_Leather_Boots_T5_QA": "贝尼安皮鞋 T5 测试版",
    "Ludvic_Leather_Boots_T4_QA": "路德维克皮鞋 T4 测试版",
    "Reeddevil_Fabric_Boots_T5_QA": "芦苇恶魔布鞋 T5 测试版",
    "Tarif_Fabric_Boots_T4_QA": "塔里夫布鞋 T4 测试版",
    "Tarif_Fabric_Boots_T3_QA": "塔里夫布鞋 T3 测试版",
    "Abidon_Fabric_Helm_T5_QA": "阿比顿布头盔 T5 测试版",
    "Scalaphynion_Fabric_Helm_T4_QA": "斯卡拉菲尼翁布头盔 T4 测试版",
    "Deer_King_Leather_Helm_T4_QA": "鹿王皮头盔 T4 测试版",
    "Tesslit_Leather_Helm_T2_QA": "特斯利特皮头盔 T2 测试版",
    "Dev_Red_Dragon_HorseArmor_Helm": "开发用红龙马铠头甲",
    "Dev_Red_Dragon_HorseArmor_Stirrup": "开发用红龙马铠马镫",
    "Dev_Red_Dragon_HorseArmor_Armor": "开发用红龙马铠护甲",
    "Dev_Red_Dragon_HorseArmor_Saddle": "开发用红龙马铠马鞍",
}


def main() -> int:
    """解析参数、校验原版资源并生成 C++ 头文件。"""
    parser = argparse.ArgumentParser(description="生成 Trinity 动态目录中文回退表")
    parser.add_argument("--game-dir", required=True, type=Path, help="当前游戏根目录")
    parser.add_argument("--output", required=True, type=Path, help="生成的 C++ 头文件")
    parser.add_argument(
        "--glyph-output",
        required=True,
        type=Path,
        help="生成供 ImGui 字体构建使用的目录字形文本",
    )
    args = parser.parse_args()

    game_dir = args.game_dir.resolve()
    _validate_game_executable(game_dir)
    assets = _extract_required_assets(game_dir)
    _validate_asset_hashes(assets)

    localization = parse_paloc(assets["localizationstring_zho-cn.paloc"]).by_key()
    item_rows, item_translations = _build_item_translations(
        assets["iteminfo.pabgb"],
        assets["iteminfo.pabgh"],
        localization,
    )
    group_rows, group_translations = _build_group_translations(
        assets["itemgroupinfo.pabgb"],
        assets["itemgroupinfo.pabgh"],
        localization,
    )
    inventory_rows, inventory_translations = _build_inventory_translations(
        assets["inventory.pabgb"],
        assets["inventory.pabgh"],
        localization,
    )
    _validate_counts(
        item_rows,
        item_translations,
        group_rows,
        group_translations,
        inventory_rows,
        inventory_translations,
    )
    _write_generated_header(
        args.output.resolve(),
        item_rows,
        item_translations,
        group_rows,
        group_translations,
        inventory_rows,
        inventory_translations,
    )
    _write_generated_glyphs(
        args.glyph_output.resolve(),
        item_translations,
        group_translations,
        inventory_translations,
    )
    print(
        "动态目录中文生成完成："
        f"物品 {len(item_translations)}/{item_rows}"
        f"（官方 {EXPECTED_ITEM_OFFICIAL_TRANSLATIONS}，"
        f"[mod] {EXPECTED_ITEM_MOD_FALLBACKS}），"
        f"分类 {len(group_translations)}/{group_rows}，"
        f"仓库 {len(inventory_translations)}/{inventory_rows}"
    )
    return 0


def _validate_game_executable(game_dir: Path) -> None:
    """严格锁定生成数据所对应的游戏主程序版本。"""
    executable = game_dir / "bin64" / "CrimsonDesert.exe"
    if not executable.is_file():
        raise FileNotFoundError(f"缺少游戏主程序：{executable}")
    actual = _sha256(executable)
    if actual != EXPECTED_GAME_EXE_SHA256:
        raise ValueError(f"游戏主程序哈希不匹配：{actual}")


def _extract_required_assets(game_dir: Path) -> dict[str, bytes]:
    """从确定的原版分包中定点提取目录表和简中 PALOC。"""
    data_assets = _extract_from_pamt(
        game_dir / "0008" / "0.pamt",
        {
            "iteminfo.pabgb",
            "iteminfo.pabgh",
            "itemgroupinfo.pabgb",
            "itemgroupinfo.pabgh",
            "inventory.pabgb",
            "inventory.pabgh",
        },
    )
    localization_assets = _extract_from_pamt(
        game_dir / "0032" / "0.pamt",
        {"localizationstring_zho-cn.paloc"},
    )
    return {**data_assets, **localization_assets}


def _extract_from_pamt(pamt_path: Path, basenames: set[str]) -> dict[str, bytes]:
    """只解析指定 basename，避免构建时扫描全部游戏索引。"""
    if not pamt_path.is_file():
        raise FileNotFoundError(f"缺少原版 PAMT：{pamt_path}")
    entries = parse_pamt_filtered(pamt_path, desired_basenames=basenames)
    by_name = {Path(entry.path).name.casefold(): entry for entry in entries}
    missing = sorted(name for name in basenames if name.casefold() not in by_name)
    if missing:
        raise ValueError(f"{pamt_path} 缺少目标：{', '.join(missing)}")
    return {
        name: extract_plaintext(by_name[name.casefold()])[0]
        for name in sorted(basenames)
    }


def _validate_asset_hashes(assets: dict[str, bytes]) -> None:
    """拒绝用未知游戏表静默生成行号错位的运行时映射。"""
    for name, expected in EXPECTED_ASSET_SHA256.items():
        actual = hashlib.sha256(assets[name]).hexdigest().upper()
        if actual != expected:
            raise ValueError(f"原版资源哈希不匹配：{name} / {actual}")


def _ordered_bounds(header: bytes, body: bytes, table_name: str) -> tuple[int, list[tuple[int, int]]]:
    """返回运行时定义数组使用的记录顺序和每条记录边界。"""
    key_size, offsets = parse_pabgh_index(header, table_name)
    if key_size not in (2, 4) or not offsets:
        raise ValueError(f"无法解析 {table_name}.pabgh")
    ordered = sorted(offsets.values())
    bounds = [
        (offset, ordered[index + 1] if index + 1 < len(ordered) else len(body))
        for index, offset in enumerate(ordered)
    ]
    return key_size, bounds


def _build_item_translations(
    body: bytes,
    header: bytes,
    localization: dict,
) -> tuple[int, list[tuple[int, str]]]:
    """按 ItemInfo 运行时行号提取物品名称的简中 PALOC 文本。"""
    key_size, bounds = _ordered_bounds(header, body, "iteminfo")
    translations: list[tuple[int, str]] = []
    used_mod_name_overrides: set[str] = set()
    for row, (start, end) in enumerate(bounds):
        record = memoryview(body)[start:end]
        cursor = key_size
        name_length = _read_u32(record, cursor, "ItemInfo string_key 长度")
        cursor += 4 + name_length
        if cursor + 1 + 8 + 1 + 8 + 4 > len(record):
            raise ValueError(f"ItemInfo row={row} 前置字段越界")
        cursor += 1 + 8
        category = record[cursor]
        localization_index = struct.unpack_from("<Q", record, cursor + 1)[0]
        _validate_localizable_default(record, cursor + 9, row, "ItemInfo")
        if category != 7:
            raise ValueError(f"ItemInfo row={row} 本地化分类异常：{category}")
        localized = localization.get(str(localization_index))
        record_key = _read_record_key(record, key_size, row)
        if localized is None or not localized.value:
            used_mod_name_overrides.add(record_key)
        translations.append((row, _item_display_name(record_key, localized)))
    unknown_mod_name_overrides = sorted(ITEM_MOD_NAME_OVERRIDES.keys() - used_mod_name_overrides)
    if unknown_mod_name_overrides:
        raise ValueError(
            "ItemInfo [mod] 中文名称表存在失效 key："
            + ", ".join(unknown_mod_name_overrides)
        )
    return len(bounds), translations


def _item_display_name(record_key: str, localized: object | None) -> str:
    """官方简中缺失时标记 Trinity 中可能不可正常添加的内部物品。"""
    localized_value = getattr(localized, "value", None)
    if isinstance(localized_value, str) and localized_value:
        return localized_value
    if not record_key:
        raise ValueError("ItemInfo 内部物品名为空，无法生成 [mod] 标记")
    translation = ITEM_MOD_NAME_OVERRIDES.get(record_key)
    if translation is None:
        raise ValueError(f"ItemInfo 缺少 [mod] 中文名称：{record_key}")
    return f"[mod] {translation}"


def _build_group_translations(
    body: bytes,
    header: bytes,
    localization: dict,
) -> tuple[int, list[tuple[int, str]]]:
    """按 ItemGroupInfo 运行时行号提取分类名称。"""
    _key_size, bounds = _ordered_bounds(header, body, "itemgroupinfo")
    translations: list[tuple[int, str]] = []
    for row, (start, end) in enumerate(bounds):
        record = memoryview(body)[start:end]
        translation = _find_group_localizable(record, localization, row)
        if translation is not None:
            translations.append((row, translation))
    return len(bounds), translations


def _find_group_localizable(record: memoryview, localization: dict, row: int) -> str | None:
    """定位 ItemGroupInfo 的首个 category=8 本地化字段。"""
    for cursor in range(max(0, len(record) - 13)):
        if record[cursor] != 8:
            continue
        localization_index = struct.unpack_from("<Q", record, cursor + 1)[0]
        localized = localization.get(str(localization_index))
        if localized is None or not localized.value:
            continue
        try:
            _validate_localizable_default(record, cursor + 9, row, "ItemGroupInfo")
        except (UnicodeDecodeError, ValueError):
            continue
        return localized.value
    return None


def _build_inventory_translations(
    body: bytes,
    header: bytes,
    localization: dict,
) -> tuple[int, list[tuple[int, str]]]:
    """按 InventoryInfo 运行时行号生成不重名的简中仓库名称。"""
    key_size, bounds = _ordered_bounds(header, body, "inventory")
    translations: list[tuple[int, str]] = []
    keys: set[str] = set()
    for row, (start, end) in enumerate(bounds):
        record = memoryview(body)[start:end]
        key = _read_record_key(record, key_size, row)
        keys.add(key)
        candidates = []
        for cursor in range(max(0, len(record) - 8)):
            localization_index = struct.unpack_from("<Q", record, cursor)[0]
            if localization_index & 0xFFFFFFFF != INVENTORY_NAME_FIELD_ID:
                continue
            localized = localization.get(str(localization_index))
            if localized is not None and localized.value:
                candidates.append(localized.value)
        if len(candidates) != 1:
            raise ValueError(f"InventoryInfo row={row} 名称候选异常：{candidates}")
        translations.append((row, INVENTORY_NAME_OVERRIDES.get(key, candidates[0])))
    unknown_overrides = sorted(INVENTORY_NAME_OVERRIDES.keys() - keys)
    if unknown_overrides:
        raise ValueError(f"InventoryInfo 缺少消歧 key：{', '.join(unknown_overrides)}")
    return len(bounds), translations


def _read_record_key(record: memoryview, key_size: int, row: int) -> str:
    """读取 PAB 记录起始处的 UTF-8 string_key。"""
    length = _read_u32(record, key_size, "InventoryInfo string_key 长度")
    start = key_size + 4
    end = start + length
    if end > len(record):
        raise ValueError(f"InventoryInfo row={row} string_key 越界")
    return bytes(record[start:end]).decode("utf-8")


def _validate_localizable_default(
    record: memoryview,
    cursor: int,
    row: int,
    table_name: str,
) -> None:
    """验证 localizable 的默认文本边界与 UTF-8 编码。"""
    length = _read_u32(record, cursor, f"{table_name} 默认文本长度")
    start = cursor + 4
    end = start + length
    if end > len(record):
        raise ValueError(f"{table_name} row={row} 默认文本越界")
    bytes(record[start:end]).decode("utf-8")


def _read_u32(data: memoryview, cursor: int, label: str) -> int:
    """带清晰错误的无符号 32 位整数读取。"""
    if cursor + 4 > len(data):
        raise ValueError(f"{label} 越界")
    return struct.unpack_from("<I", data, cursor)[0]


def _validate_counts(
    item_rows: int,
    items: list[tuple[int, str]],
    group_rows: int,
    groups: list[tuple[int, str]],
    inventory_rows: int,
    inventories: list[tuple[int, str]],
) -> None:
    """锁定当前游戏表规模，防止部分解析也生成可加载产物。"""
    actual = (
        item_rows,
        len(items),
        group_rows,
        len(groups),
        inventory_rows,
        len(inventories),
    )
    expected = (
        EXPECTED_ITEM_ROWS,
        EXPECTED_ITEM_ROWS,
        EXPECTED_GROUP_ROWS,
        EXPECTED_GROUP_TRANSLATIONS,
        EXPECTED_INVENTORY_ROWS,
        EXPECTED_INVENTORY_TRANSLATIONS,
    )
    if actual != expected:
        raise ValueError(f"动态目录记录数不匹配：{actual} != {expected}")
    item_mod_fallbacks = sum(translation.startswith("[mod] ") for _, translation in items)
    item_official_translations = len(items) - item_mod_fallbacks
    if (
        item_official_translations != EXPECTED_ITEM_OFFICIAL_TRANSLATIONS
        or item_mod_fallbacks != EXPECTED_ITEM_MOD_FALLBACKS
    ):
        raise ValueError(
            "物品名称来源计数不匹配："
            f"官方 {item_official_translations}/{EXPECTED_ITEM_OFFICIAL_TRANSLATIONS}，"
            f"[mod] {item_mod_fallbacks}/{EXPECTED_ITEM_MOD_FALLBACKS}"
        )


def _write_generated_header(
    output: Path,
    item_rows: int,
    items: list[tuple[int, str]],
    group_rows: int,
    groups: list[tuple[int, str]],
    inventory_rows: int,
    inventories: list[tuple[int, str]],
) -> None:
    """把动态目录中文映射写成只读 C++ 数组。"""
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "// 此文件由 generate_catalog_translations.py 生成，请勿手工修改。",
        "#pragma once",
        "#include <cstddef>",
        "#include <cstdint>",
        "namespace trinity_cn::generated_catalog {",
        "struct CatalogTranslation { std::uint16_t row; const char* translation; };",
        f"inline constexpr std::uint32_t kExpectedItemRowCount = {item_rows};",
        f"inline constexpr std::uint32_t kExpectedGroupRowCount = {group_rows};",
        f"inline constexpr std::uint32_t kExpectedInventoryRowCount = {inventory_rows};",
        "inline constexpr CatalogTranslation kItemTranslations[] = {",
    ]
    lines.extend(
        f"    {{ {row}, {_cpp_utf8_literal(translation)} }},"
        for row, translation in items
    )
    lines.extend(
        [
            "};",
            "inline constexpr CatalogTranslation kGroupTranslations[] = {",
        ]
    )
    lines.extend(
        f"    {{ {row}, {_cpp_utf8_literal(translation)} }},"
        for row, translation in groups
    )
    lines.extend(
        [
            "};",
            "inline constexpr CatalogTranslation kInventoryTranslations[] = {",
        ]
    )
    lines.extend(
        f"    {{ {row}, {_cpp_utf8_literal(translation)} }},"
        for row, translation in inventories
    )
    lines.extend(
        [
            "};",
            "inline constexpr std::size_t kItemTranslationCount = sizeof(kItemTranslations) / sizeof(kItemTranslations[0]);",
            "inline constexpr std::size_t kGroupTranslationCount = sizeof(kGroupTranslations) / sizeof(kGroupTranslations[0]);",
            "inline constexpr std::size_t kInventoryTranslationCount = sizeof(kInventoryTranslations) / sizeof(kInventoryTranslations[0]);",
            "}",
        ]
    )
    output.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _cpp_utf8_literal(value: str) -> str:
    """避免源文件转义歧义，统一输出 UTF-8 十六进制字节。"""
    return '"' + "".join(f"\\x{byte:02X}" for byte in value.encode("utf-8")) + '"'


def _write_generated_glyphs(
    output: Path,
    items: list[tuple[int, str]],
    groups: list[tuple[int, str]],
    inventories: list[tuple[int, str]],
) -> None:
    """输出动态目录使用的全部 BMP 字形，供构建脚本合并进字体范围。"""
    glyphs = sorted(
        {character for _, text in (*items, *groups, *inventories) for character in text}
    )
    unsupported = [character for character in glyphs if ord(character) > 0xFFFF]
    if unsupported:
        raise ValueError(f"动态目录包含 ImGui 当前不支持的补充平面字符：{unsupported[0]}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(glyphs), encoding="utf-8", newline="\n")


def _sha256(path: Path) -> str:
    """流式计算大文件 SHA-256，避免把游戏 EXE 整体读入内存。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest().upper()


if __name__ == "__main__":
    raise SystemExit(main())
