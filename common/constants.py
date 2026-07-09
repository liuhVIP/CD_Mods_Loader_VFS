"""独立加载器常量集中定义。"""

from __future__ import annotations

# 用户放置已解压模组的目录名。
MODS_DIR_NAME = "mods"

# 独立加载器的工作目录名，避免与完整管理器 CDMods 状态互相污染。
WORK_DIR_NAME = ".cdloader"

# 保存干净游戏文件备份的子目录名。
VANILLA_DIR_NAME = "vanilla"

# 事务性写入时使用的暂存目录名。
STAGING_DIR_NAME = "staging"

# 运行日志保存目录名。
LOGS_DIR_NAME = "logs"

# 首次真实加载使用的日志文件名，新的冷加载日志会覆盖旧文件。
COLD_LOAD_LOG_FILE_NAME = "cold_load.log"

# 后续真实加载使用的日志文件名，新的热加载日志会覆盖旧文件。
HOT_LOAD_LOG_FILE_NAME = "hot_load.log"

# 状态文件名，只记录恢复、清理和诊断所需的最小信息。
STATE_FILE_NAME = "state.json"

# PAMT 目标命中缓存文件名，只保存模组实际查过的目标，不保存全量游戏索引。
PAMT_TARGET_CACHE_FILE_NAME = "pamt_target_cache.json"

# PAMT 目标命中缓存 schema 版本，结构变化时递增以强制重建。
PAMT_TARGET_CACHE_SCHEMA = 2

# 冷启动构建 PAMT 索引时并行解析的线程数，只读原版 0.pamt，不参与游戏文件写入。
PAMT_INDEX_WORKER_COUNT = 4

# 可选加载顺序文件名。优先读取 .cdloader/load_order.json，兼容旧的 mods/load_order.json。
LOAD_ORDER_FILE_NAME = "load_order.json"

# 游戏元数据目录名。
META_DIR_NAME = "meta"

# 游戏可执行文件所在目录名，用于判断加载器是否放在游戏根目录。
GAME_BIN_DIR_NAME = "bin64"

# 红色沙漠主程序文件名，用于判断加载器是否放在游戏根目录。
GAME_EXECUTABLE_NAME = "CrimsonDesert.exe"

# PAPGT 主索引文件名。
PAPGT_FILE_NAME = "0.papgt"

# PATHC 纹理索引文件名，第一阶段只备份/恢复，不主动改写。
PATHC_FILE_NAME = "0.pathc"

# overlay 输出的 PAZ 文件名。
OVERLAY_PAZ_NAME = "0.paz"

# overlay 输出的 PAMT 文件名。
OVERLAY_PAMT_NAME = "0.pamt"

# 独立 overlay 目录起始编号；0036 通常给独立 PAZ 模组使用，文档建议从 0037 起。
OVERLAY_START_DIR = 37

# 状态文件 schema 版本，后续结构变化时递增。
STATE_SCHEMA = 1

# 四位数字游戏目录名长度。
GAME_DIR_NAME_LENGTH = 4

# 支持识别的 JSON 模组扩展名。
JSON_SUFFIX = ".json"

# loose game files 模组常用的外层目录名，第二阶段先只扫描提示不参与 apply。
LOOSE_FILES_DIR_NAME = "files"

# 兼容部分模组管理器导出的 loose game files 外层目录名，当前只用于扫描提示。
GAME_FILES_DIR_NAME = "game_files"

# DDS 纹理文件扩展名，后续接 PATHC 更新时使用。
DDS_SUFFIX = ".dds"

# 扫描 loose files 时用于识别“直接从游戏相对路径开始”的常见顶层目录名。
KNOWN_GAME_TOP_DIRS = frozenset(
    {
        "character",
        "effect",
        "environment",
        "font",
        "gamedata",
        "level",
        "prefab",
        "sequencer",
        "sound",
        "ui",
    }
)

# 需要提示用户手动解压的压缩包扩展名。
ARCHIVE_SUFFIXES = frozenset({".zip", ".7z", ".rar"})

# PAZ/PAMT/PAPGT 完整性哈希使用的固定 seed。
HASH_SEED = 0xC5EDE

# PAZ entry 对齐字节数。
PAZ_ALIGNMENT = 16

# JMM BuildMultiPamt 兼容常量。
PAMT_CONSTANT = 0x610E0232

# 事务提交前原文件备份后缀。
PRE_APPLY_SUFFIX = ".pre-apply"

# 精简模式进度条展示的 apply 阶段名，只用于控制台进度提示，不参与业务判断。
APPLY_PROGRESS_PHASES = (
    "初始化加载环境",
    "扫描 mods",
    "准备 meta 读取",
    "构建 loose overlay 输入",
    "构建 JSON overlay 输入",
    "构建 Format 3 overlay 输入",
    "收集 standalone 归档",
    "构建 overlay PAZ/PAMT",
    "构建 PATHC",
    "构建 PAPGT",
    "事务写入游戏目录",
    "保存加载状态",
)
