# AGENTS.md

本文件用于给后续 AI / Codex 说明独立版 Crimson Desert 模组加载器 `cdmm` 的运行方式、底层原理、已踩坑点，以及 Format 3 模组尚未完成的范围。所有沟通默认使用中文，所有新增或修改文件使用 UTF-8 编码。

## 版本号规范

- 项目根目录 `version.txt` 是普通加载器与 VFS 加载器发布版本号的唯一来源。
- VFS 成品文件名、控制台标题、错误提示以及 Windows PE 的 FileVersion/ProductVersion 必须由 `version.txt` 派生，禁止在 Python 或 PowerShell 中重复硬编码版本号。
- 简洁版本（例如 `v3`、`v1.1`）在 PE 元数据中依次补零为四段数字（例如 `3.0.0.0`、`1.1.0.0`）。

## VFS 成品打包强制基线

- VFS 正式成品禁止使用 Nuitka `--onefile`。2026-07-12 已实机确认：相同业务代码打成单文件后，Nuitka 会先释放到临时目录再启动，Crimson Desert 的 Steam / ASI / 保护层启动链会失败；改回 `--standalone` 目录版后可正常进入游戏。
- VFS 目录版固定结构如下，外层 EXE 只负责转发到真实 core：

```text
cdloader-VFS-v<版本>.exe
cdloader/
  cdloader-vfs-core.exe
  cdmm/private/vfs_runtime/
```

- `tools/vfs_launcher.py::resolve_game_dir()` 必须同时检查 core 自身目录和父目录。目录版真实进程位于 `游戏根目录\cdloader\cdloader-vfs-core.exe`；如果只检查 `executable_dir()`，会错误提示“未识别到游戏根目录”，即使外层 EXE 已正确放在游戏根目录。
- 后续不得再次删除 `for candidate in (exe_dir, exe_dir.parent)` 的父目录识别逻辑。
- 每次 VFS 目录版打包至少运行 `test/test_vfs_launcher_command.py`，其中必须保留“core 位于一级发布目录时解析回游戏根目录”的回归测试。
- 2026-07-12 实机确认：修复父目录识别后的目录测试包可正常进入游戏。单文件模式不可作为回退方案；VirusTotal/Nexus 是否放行不能凌驾于实际可启动性。

## Physical 实体加载强制基线

- Physical 是 Windows 10 等无法稳定使用 native VFS runtime 用户的兼容入口。它只复用 VFS 的模组合成与 DMM-like 分包构建结果，然后把产物真实写入游戏目录；启动游戏时禁止调用 `nppvfs_launcher.exe`、`--asi-load`、`vfs_runtime.dll` 或任何 VFS Hook/注入链路。
- Physical 与 VFS 必须共用 `services/vfs_loader.py::build_vfs_package()` 生成的最终产物，禁止恢复旧 `services/loader.py::apply_loader()` 单数字 overlay 管线。相同游戏版本、语言、mods 和加载顺序下，两种模式的 `nppv3_*`、`nppvoice`、`nppgen`、`nppsa` PAZ/PAMT 字节以及 PAPGT 注册顺序必须一致。
- `nppv3_*` 保存已拆分的 Format 3 表结果，`nppvoice` 保存 `.wem` 语音 loose，`nppgen` 保存传统 JSON/通用表补丁，`nppsa` 保存其余 loose 与 `.cdmod` 文件替换。`nppsa` 很大通常是大量 PAC、DDS、PAA、UI、模型或动画资源合成后的正常结果，不能因为目录大就重新塞回原始编号包。
- 模组提供 `files/0012/...` 或根部 `0012/...` loose 文件时，`0012` 只用于定位原版 PAMT entry；最终修改内容写入 `nppsa`，严禁改写游戏原始 `0012/0.paz` 或 `0012/0.pamt`。这一规则适用于所有原始 `0000-0035` 目录。
- 模组自带完整 `NNNN/0.paz + NNNN/0.pamt` 时属于 standalone archive，必须保留完整包并分配安全的新四位数字目录（通常从 `0037` 起）。因此 Physical 同时出现 `npp*` 与 `0037/0040/0043` 等目录是合法结构：前者是合成分包，后者是 standalone，不得误判为旧数字 overlay。
- Physical 从始至终不得覆盖游戏原始编号 PAZ/PAMT。它对游戏源文件的覆盖只允许是 `meta/0.papgt`，存在 DDS/PATHC 变化时再覆盖 `meta/0.pathc`；`npp*` 和新分配的四位数字目录均为加载器新增文件。两个 meta 文件写入前必须由 `VanillaStore` 保存当前游戏版本的 vanilla 备份。
- Physical 必须使用 `Transaction` 事务提交 npp/standalone/meta。新文件全部提交成功后，才能清理状态中记录的废弃 `npp*`、standalone 或 v9.2 早期 `0039/0040` 单数字 overlay；不得先删旧产物再构建，避免失败后游戏既无法恢复也无法启动。
- `.cdloader/state.json` 必须记录 `physical_output_files`、`physical_output_dirs` 和 `standalone_dirs`；`.cdloader/physical_mode_state.json` 必须记录模组指纹、加载器版本、PALOC 语言、游戏 EXE 修改时间及实体输出快照。缓存命中时直接原生/Steam 启动，不重复构建或复制大型分包。
- 游戏更新、Steam 校验、语言变化、模组新增/删除/修改/排序变化或实体输出缺失时，Physical 缓存必须失效。游戏更新后只能把 Steam 实际替换的新版本 meta 刷新为 vanilla 备份，严禁把仍处于 Physical 状态的旧实体 meta 收录成新 vanilla。
- 用户删除全部 mods 后再次运行 Physical，必须恢复 vanilla PAPGT/PATHC 并清理全部加载器新增目录。`--revert` 同样必须恢复正确游戏版本的 vanilla meta、删除所有记录的 `npp*` 与 standalone、清除 Physical 模式锁；禁止仅删除 `physical_mode_state.json` 冒充恢复。
- Physical active/pending 状态必须阻止 VFS 构建和启动，因为游戏实体 meta 已被修改；只有完整 `--revert` 成功后才能解除互斥锁并重新允许 VFS。
- Physical 冷构建必须复用 VFS 的控制台阶段回调，并额外显示实体文件名、大小、事务提交、旧目录清理和状态保存。耗时阶段至少每 3 秒输出一次“仍在处理”，避免低性能电脑用户误以为程序卡死。
- Physical 与 VFS 两个外层 EXE 共用 standalone 目录版 `cdloader/cdloader-vfs-core.exe`。版本号、PE 元数据和文件名仍只能由 `version.txt` 派生；每次修改 Physical 至少运行 `test/test_physical_launcher.py`、`test/test_vfs_launcher_command.py`、完整 `test/` 与 `ruff check .`。

## 项目定位

- 当前目录：`T:\python_pro\cdmm`
- 这是独立命令行加载器，不是完整 GUI 管理器 `T:\python_pro\CrimsonDesert-UltimateModsManager`。
- 目标游戏通常位于：`G:\SteamLibrary\steamapps\common\Crimson Desert`
- 模组目录通常位于：`G:\SteamLibrary\steamapps\common\Crimson Desert\mods`
- 当前独立版已经验证：传统 JSON byte patch、`files/NNNN/...`、根部 `NNNN/...`、`files/gamedata/...`、根部游戏路径 loose files、DDS PATHC 更新、standalone `0.paz + 0.pamt`、meta 统一重建、以及 `iteminfo.pabgb` 的一类 Format 3 嵌套字段窄支持可以正常组合加载。
- 2026-07 起，`Format 3` 不再只是一层临时桥接；当前已经拆成“解析层 + 能力声明层 + 运行时分发层 + table writer”结构，后续扩展必须沿这个结构继续推进，不要把新逻辑重新塞回单一大函数里。

## 本机运行约定

- PowerShell 7：
  `C:\Program Files\PowerShell\7\pwsh.exe`
- 优先使用项目虚拟环境 Python：
  `T:\python_pro\cdmm\.venv\Scripts\python.exe`
- 备用 Python：
  `E:\python\UV\uvpython\cpython-3.10.18-windows-x86_64-none\python.exe`
- 用户入口通常是：
  `T:\python_pro\cdmm\run_cdmm.bat`
- `run_cdmm.bat` 只做启动包装，实际逻辑走 `run_cdmm.ps1`，避免 `.bat` 中文编码问题。
- 当前已支持打包为单体控制台 exe：
  `T:\python_pro\cdmm\dist\cdloader.exe`
- 模组加载顺序优先由游戏目录下 `.cdloader/load_order.json` 控制；没有该文件时默认按文件名升序并自动生成。旧的 `mods/load_order.json` 仍兼容，但 `.cdloader/load_order.json` 优先级更高。该文件格式是 JSON 字符串数组，可写模组目录名、JSON 文件名，或相对 `mods` 的路径。数组越靠下加载越晚，覆盖优先级越高；同一个最终游戏路径冲突时，必须由这里的最终顺序决定谁覆盖谁。每次扫描/加载都会自动同步：缺失的新模组按文件名补到末尾，已经不存在的模组会从排序文件中移除。
- 2026-07-08 存档无响应排障结论已修正：48 个模组集合仍会在加载存档即将进入时无响应，不能再当成稳定。`vfs_state.json` 指纹为 `9df9d354378faa59665c268174001f25c3cfd0ebb7f716eadb144cc63c26b4e3`，`nppsa/0.paz` 约 505 MB。之前记录的 28 项排序只作为较小参考集合/回退起点，完整列表在 `docs/save-load-hang-triage.md`。
- 后续不要回退顺序链路：排序文件必须只保留实际参与加载的模组，自动补新增、清理缺失/不可识别/重复项；loose 和 standalone 不能再绕过 `scan_mods()` 自行按目录名排序。临时禁用模组用于二分时，使用 `.cdloader/disabled_mods.json`，不要靠删除 `load_order.json` 项，因为缺失项会被自动补回。
- 已修复 disabled_mods 失效问题：传入 `scan_mods()` 最终顺序后，`loose_file_service` 与 `standalone_archive_service` 只能枚举该顺序里的目录，不能再把 `mods` 下其他目录补回。不要恢复“补齐所有目录”逻辑，否则被禁用的 loose/DDS/standalone 仍会进入 VFS。
- VFS 启动不应每次强制重建。`build_vfs_package()` 会先扫描并同步加载顺序，再比较当前模组指纹、`.cdloader/vfs_state.json`、`allow_missing_targets` 和 `vfs_active` 映射文件完整性；全部一致时直接复用旧 `vfs_mapping_tree.json` 和 `vfs_active`，只在模组/排序/参数/缓存产物变化时冷构建。
- 成品 exe 的定位是直接复制到游戏根目录运行，例如：
  `G:\SteamLibrary\steamapps\common\Crimson Desert\cdloader.exe`
- 成品 exe 无参数启动时会立刻判断自身所在目录是否为游戏根目录。判断依据是：
  `bin64\CrimsonDesert.exe`
- 打包后的 exe 不读取开发用的 `config\game_config.json`。`config\game_config.json` 只用于源码开发阶段不传 `--game-dir` 时兜底。
- 如果成品 exe 不在游戏根目录，无参数启动时应直接提示用户把 exe 放到游戏根目录，不进入菜单。

常用命令：

```powershell
Set-Location 'T:\python_pro\cdmm'
& '.\.venv\Scripts\python.exe' -m pytest
& '.\.venv\Scripts\python.exe' -m pytest tests\test_cdmm_loader.py
& '.\.venv\Scripts\python.exe' -m ruff check .
```

源码开发阶段命令：

```powershell
Set-Location 'T:\python_pro'
& '.\cdmm\.venv\Scripts\python.exe' -m cdmm.cli apply --game-dir 'G:\SteamLibrary\steamapps\common\Crimson Desert'
& '.\cdmm\.venv\Scripts\python.exe' -m cdmm.cli scan --game-dir 'G:\SteamLibrary\steamapps\common\Crimson Desert'
& '.\cdmm\.venv\Scripts\python.exe' -m cdmm.cli revert --game-dir 'G:\SteamLibrary\steamapps\common\Crimson Desert'
```

打包命令：

```powershell
Set-Location 'T:\python_pro\cdmm'
& 'C:\Program Files\PowerShell\7\pwsh.exe' -NoLogo -NoProfile -ExecutionPolicy Bypass -File '.\build_cdloader.ps1'
```

成品 exe 命令行调用方式：

```powershell
# 推荐：cdloader.exe 放到游戏根目录后，无参数双击/运行进入菜单。
& 'G:\SteamLibrary\steamapps\common\Crimson Desert\cdloader.exe'

# 外部 Python 客户端或脚本调用时可以显式传游戏根目录。
& 'T:\python_pro\cdmm\dist\cdloader.exe' apply --game-dir 'G:\SteamLibrary\steamapps\common\Crimson Desert'
& 'T:\python_pro\cdmm\dist\cdloader.exe' scan --game-dir 'G:\SteamLibrary\steamapps\common\Crimson Desert'

# revert 仍是 CLI 兼容命令，不显示在普通菜单里。
& 'T:\python_pro\cdmm\dist\cdloader.exe' revert --game-dir 'G:\SteamLibrary\steamapps\common\Crimson Desert'
```

## 菜单行为

成品 exe 无参数启动且确认位于游戏根目录后，显示标题：

```text
红色沙漠独立轻量模组加载器(b站up改名开发)—版本v1.0
```

菜单只有三项：

- `1. 开始加载模组`：执行 apply，扫描并写入 overlay / meta。默认使用 tqdm 进度条，不在控制台刷详细警告。
- `2. 只扫描 mods，不写入游戏文件`：用于观察识别结果。
- `3. 退出`

执行完成后应回到菜单。不要再恢复旧的“开发者模式”菜单，也不要把 `revert` 放回普通菜单；`revert` 只保留给 CLI 调用。

真实加载成功时控制台通常只出现精简结果：

```text
加载完成：overlay 已写入 0039
完成时间：12.34s
```

详细过程、warning、error 应写入游戏目录下：

```text
.cdloader\logs\cold_load.log
.cdloader\logs\hot_load.log
.cdloader\logs\scan.log
```

真实加载日志只保留两个覆盖文件：

- 首次或没有 `state.json` / `last_fingerprint` 时写 `cold_load.log`。
- 后续已有 `last_fingerprint` 时写 `hot_load.log`。
- 同类新日志覆盖旧日志，不要无限追加生成新日志文件。

典型成功结果：

```text
加载完成：overlay 已写入 0039
```

这里的 `0039` 只是本次新建 overlay 目录编号，不代表游戏原始最大编号必须是 `0039`。用户经常会把游戏目录和 `.cdloader` 恢复纯净，原始游戏最大编号可能只到 `0035`。

## 核心工作目录

加载器会在游戏根目录下使用：

```text
.cdloader/
```

典型职责：

- 保存原始 meta / vanilla 信息备份。
- 保存 staging 临时构建内容。
- 保存上次 apply 的 state，供 revert 使用。
- 防止直接破坏原始游戏文件。

注意：用户测试时经常会直接删除 `.cdloader`，所以逻辑不能依赖旧缓存一定存在。

## 模组加载底层原理

Crimson Desert 主要通过 `meta/0.papgt` 找到各编号目录里的 `0.pamt`，再由 `0.pamt` 索引 `0.paz` 内的文件 entry。

加载器的原则不是直接改游戏原始 PAZ，而是构建一个新的 overlay 编号目录：

```text
NNNN/
  0.pamt
  0.paz
```

然后重新生成或更新：

```text
meta/0.papgt
meta/0.pathc
```

核心原则：

- `0.papgt` 负责让游戏知道有哪些 `NNNN/0.pamt`。
- `0.pamt` 负责描述 `0.paz` 中每个文件的 hash、path、offset、size 等索引信息。
- `0.paz` 存放实际 overlay 文件内容。
- 同一个 entry path 最终游戏会读取后出现的覆盖版本，所以多个模组改同一个真实文件时，必须先在加载器内合成成一个最终文件，再写入一个 overlay entry。
- 不要让 loose replacement、JSON patch、Format 3 patch 各自写同一个目标文件互相覆盖。
- 如果不同源 `entry_path` 最终解析到同一个 PAMT 路径（`dir_path + filename`），必须按最终路径去重并保留后加载版本；“后加载”只看 `scan_mods()` 产出的最终顺序，也就是优先来自 `.cdloader/load_order.json` 的顺序，不能在 overlay 层另起一套排序。发生覆盖时必须写出 `overlay 最终路径覆盖` 日志，包含旧来源、新来源、最终路径和文件大小。不要只按原始 `entry_path` 去重，否则可能留下重复 PAMT 记录，导致 PATHC 按大 DDS 登记、游戏却读到小 DDS entry，出现 PAZ 越界或存档卡死。

## Apply 推荐流程

当前独立加载器的稳定组合顺序应保持如下思路：

1. `scan_mods()` 扫描 mods 目录，识别 JSON、loose files、DDS、standalone PAZ/PAMT、meta 等组件。
2. `VanillaStore.ensure_meta_backup()` 准备原始 meta / vanilla 索引。
3. `build_loose_overlay_entries()` 先处理 loose files，形成 overlay 输入。
4. `build_json_overlay_entries(..., base_entries=loose_overlay_inputs)` 再处理传统 JSON byte patch，并把 JSON patch 叠加到 loose base 上。
5. `build_format3_overlay_entries(..., base_entries=[loose, json])` 处理 Format 3；当前只适合已实现 writer 的表，未实现的 intent 必须明确跳过并警告。Format 3 必须叠加到 loose/JSON 已合成的 base 上。
6. `collect_standalone_archives()` 收集 standalone `0.paz + 0.pamt` 组件。
7. `build_overlay()` 写入新的 overlay `NNNN/0.paz + 0.pamt`。
8. `build_pathc_for_overlay()` 针对 DDS 更新 `meta/0.pathc`。
9. `build_papgt()` 统一重建 `meta/0.papgt`，注册 overlay 和 standalone PAMT。
10. `Transaction` 原子写入，失败时尽量恢复。

重要：第 3、4 步顺序不能随便换。已经验证过 `UniEquip` 的 loose `iteminfo.pabgb` 需要先作为 base，然后 `FemaleArmorModule.json` 等 JSON patch 再叠加，否则后写的 JSON 会从 vanilla 重建并覆盖 loose replacement。

## 已支持的模组形态

### 传统 JSON byte patch

典型字段：

```json
{
  "game_file": "gamedata/inventory.pabgb",
  "patches": [
    {
      "offset": 123,
      "original": "...",
      "patched": "..."
    }
  ]
}
```

处理要点：

- 这是 byte patch，不依赖 `crimson_rs`。
- 第一版加载器已经能稳定让这类 JSON 生效。
- 如果 `original` 不匹配但附近可重定位，应允许偏移重定位。
- 如果部分 patch 未匹配，可以半应用并警告。
- 如果目标 bytes 已经是 `patched`，不要简单认为无需输出 overlay；要确认最终合成文件仍被写入。

### loose files

已支持：

```text
ModName/files/0012/...
ModName/0012/...
ModName/files/gamedata/...
ModName/gamedata/...
ModName/ui/...
ModName/sequencer/...
```

已验证的实际例子：

```text
Better_Inventory_and_Trade_UI/files/0012/...
FemaleHumanIcon/0012/...
UniEquip - 1.05.01 Update/files/gamedata/...
sequencer/baseseq/gamesystemfx/ui/cd_seq_ui_loading.paseq
```

处理要点：

- `files/NNNN/...` 是编号目录 loose。
- 根部 `NNNN/...` 也要支持，不能只报警“暂未加载”。
- `files/gamedata/...` 不是编号目录，属于 root game path loose，也要枚举。
- `gamedata/binary__/client/bin/iteminfo.pabgb` 这类路径可能要按 basename 或真实 vanilla entry 映射到 `gamedata/iteminfo.pabgb`。
- `.paseq` 不是 DDS，不需要 PATHC，但仍应作为普通 loose game file 进入 overlay。

### standalone PAZ/PAMT

已支持形态：

```text
ModName/0036/0.paz
ModName/0036/0.pamt
```

或类似：

```text
Skip Character walking Loading Scene/
  0036/
    0.pamt
    0.paz
  modjson/
```

处理要点：

- 这类模组本身已经带完整 `0.paz + 0.pamt`。
- 加载器应分配或复用安全的新编号目录，复制该 archive，并把对应 PAMT 注册进统一生成的 `meta/0.papgt`。
- 不要直接信任模组自带 `meta/0.papgt` 覆盖游戏 meta。

### meta/0.papgt 与 meta/0.pathc

部分模组会自带：

```text
meta/0.papgt
meta/0.pathc
```

当前策略：

- 不直接覆盖游戏 meta。
- 加载器统一重建 `0.papgt`。
- DDS 相关路径由加载器统一更新 `0.pathc`。

原因：

- 多模组同时安装时，直接使用某个模组自带 meta 会覆盖掉其他模组注册信息。
- 独立加载器必须把 JSON、loose、standalone、DDS 统一合并到同一套 meta。

### DDS 与 PATHC

DDS 文件进入 overlay 只是第一步。游戏还依赖：

```text
meta/0.pathc
```

处理要点：

- 扫描 loose DDS。
- 将 DDS overlay entry path 更新到 PATHC 映射。
- 编号 loose DDS 如果在指定 `NNNN/0.pamt` 中找不到，不要立刻认定“按原始路径写入”就一定能生效。应先用原版 `meta/0.pathc` 验证是否存在更真实的纹理路径；例如模组给 `files/0032/character/name.dds`，而 PATHC 只注册 `/character/texture/name.dds`，则应把 overlay entry 和 PATHC 映射都落到 `character/texture/name.dds`。这一规则必须基于 PATHC hash 命中，不能按某个模组名或具体文件名硬编码。
- 已验证真实组合：`Nude_Damiane + Body_Slider_Mod + Skin 8k_Shaved_Natural Tan + Skin 8k_Stubble_Cum`。其中两个 Skin addon 都提供 `cd_phw_00_nude_00_0001.dds`；`.cdloader/load_order.json` 中 `Skin 8k_Stubble_Cum` 排在 `Skin 8k_Shaved_Natural Tan` 下面，加载更晚，最终同 `entry_path`/同最终 PAMT 路径由 Stubble 覆盖 Shaved，Stubble 生效。关键修复不是调整排序，而是把缺失 PAMT 的 DDS 从原始 `character/...` 推断到 PATHC 已存在的 `character/texture/...`。
- 已验证日志类似：

```text
INFO: PATHC: 更新 3 条 DDS 映射，新增 1 条 DDS record，跳过 0 条
```

- 如果 DDS 不更新 PATHC，功能文件可能生效，但图标、贴图、UI 样式会异常。

## PABGB / PABGH 注意事项

很多数据表是成对出现：

```text
xxx.pabgb
xxx.pabgh
```

要点：

- `.pabgb` 是主要数据。
- `.pabgh` 常保存行偏移或索引信息。
- 如果 `.pabgb` 的行长度发生变化，必须同步修 `.pabgh` offset。
- 只改 `.pabgb` 不修 `.pabgh`，游戏可能读错行、无效果或崩溃。
- JSON byte patch 如果只是等长替换，通常不需要重建 `.pabgh`。
- Format 3 或 list writer 改变序列化长度时，必须特别小心 `.pabgh`。

## 已踩坑记录

### 旧 overlay 污染 basename 匹配

之前出现过 `kliff_damiane_RTP_current_update.json` 的 `characterinfo.pabgb` 被误匹配到旧 overlay 的 `ui/characterinfo.pabgb / 0039`。

正确策略：

- JSON patch target 查找优先低编号 vanilla。
- 优先完整路径，其次 `gamedata/` 语义路径，最后才 basename。
- 不要优先扫描旧 overlay 或 `.cdloader` 生成物。

### loose replacement 与 JSON patch 同目标覆盖

已经确认：

- `UniEquip - 1.05.01 Update/files/gamedata/.../iteminfo.pabgb` 是 loose replacement。
- `FemaleArmorModule.json` 也会 patch `gamedata/iteminfo.pabgb`。

正确策略：

- loose 先作为 base。
- JSON patch 叠加在 loose base 上。
- 最终只写一个合成后的 `gamedata/iteminfo.pabgb` overlay entry。

### `files/gamedata/...` 不能当作编号目录

`files/0012/...` 和 `files/gamedata/...` 是两类不同结构。后者要按游戏根路径 loose 处理，否则 `UniEquip` 这类模组会被识别但游戏无效果。

### 根部编号目录也要加载

`FemaleHumanIcon/0012/...` 最初只报警：

```text
发现根部编号 loose files 组件（暂未加载）
```

后来补支持后已确认生效。

### `sequencer/...` 不是 DDS 也要支持

例如：

```text
sequencer/baseseq/gamesystemfx/ui/cd_seq_ui_loading.paseq
```

这类是普通 loose game file，不需要 PATHC，但必须打入 overlay。

### `.cdloader` 可被用户删除

用户经常为了纯净测试删除 `.cdloader`。加载器必须能从纯净游戏目录重新建立备份和 overlay，不能依赖旧 state 才能 apply。

### 游戏目录原始最大编号不是判断标准

用户会删除旧 overlay，原始游戏目录可能最大只有 `0035`。日志里的 `overlay 已写入 0037`、`0039` 是加载器新分配结果，不代表旧版本残留一定存在。

### 不要把 pycache 变化当成业务改动

运行测试或 Python 后会产生大量：

```text
__pycache__/*.pyc
```

这些不是业务逻辑，不要为了清理它们去回滚用户改动，也不要把它们纳入功能说明。

### test 目录不要求纳入 git

本项目的 `test/` 目录可以用于本地回归验证，但用户已明确：`test` 相关文件不需要纳入 git 提交。后续 AI 可以继续补/改本地测试并运行 pytest，但不要因为 `test/` 未出现在 `git status` 或没有被 git 跟踪而困惑，也不要把“测试文件未提交”当成未完成事项。最终说明里只需要报告测试是否运行、是否通过。

## 2026-07 VFS 实验模式与多模组稳定性记录

这一轮 VFS 调试的目标是：在游戏启动前注入虚拟文件层，让模组通过 `.cdloader/vfs_active` 映射生效，避免反复改写游戏源文件。当前结论是“可行但仍需继续优化稳定性”，尤其是多 JSON、大 overlay、ASI 原生插件共存时的启动无响应问题。

### VFS 闪退统一排障入口

- 后续遇到启动闪退、随机失败、`2/12`、存档后崩溃、`AppHangB1`、内存不足、PAZ/PAMT 读取异常或坏模组卸载后仍失败，必须先阅读 `docs/CrimsonDesert-VFS-闪退与启动不稳定排障经验.md`，按症状分流并冻结现场。
- 不得把内存压力、Steam 冷启动、Hook 线程竞争、固定活动路径/硬链接文件对象关联、资源型模组崩溃、实体包污染和 crashpad 噪声混为同一个问题；相同错误表现可能来自不同层，必须以游戏阶段、WER、指纹、快照路径和分包哈希为证据。

### 2026-07-15 VFS 随机启动失败线程安全修复

- 用户最初复现同一套 VFS 连续“失败、失败、失败、成功”，等待后偶尔进入游戏。失败/成功使用相同 `vfs_state` 指纹、相同 VFS 产物和 `files=30 / directories=15` 映射，因此不能继续把这类随机结果优先归因于 `.cdmod`、PAZ/PAMT 或加载顺序。
- 根因方向是 native Hook runtime 的共享状态竞态：句柄追踪、重定向句柄/模块、Win32/NT 搜索状态、虚拟当前目录和日志流均可能被游戏多个线程并发访问；NT 目录状态还可能在关闭与枚举并发时被释放。
- 修复后共享状态统一使用 `SRWLOCK`，查询在锁内复制结果；NT 目录状态使用 `shared_ptr + 每句柄独立 SRWLOCK`；日志增加线程锁和按路径生成的跨进程 Mutex。`run_target.ps1` 归档日志时有限重试，占用不释放则复制快照，日志归档不再阻断启动。
- 验证基线：Debug/Release 构建及 sandbox 回归通过；8 线程 x 2,000 轮压力测试 0 失败；跨进程详细日志新增 20,148 行且 0 损坏；用户用相同 30/15 映射连续实机启动 4 次，全部达到 `(12/12)`、`End Load SaveSlot1`、正常退出且无 `[Crash]` 或 RenderPass/Pipeline 错误。
- 已同步并打包到 v6。runtime SHA-256 为 `C0260479F35A3E6CE3BE896EA68BD51DBE991C3ACFC125EB465751EB8DB1B692`；`dist_nuitka/cdloader-VFS-v6.zip` SHA-256 为 `0FDCC1CFDC42FCD92DE11C4C79CDB0C48D8190528ACA8F036AC630000337E394`。
- 后续若“相同指纹 + 相同映射 + 相同产物”仍随机成功/失败，必须先保存失败/成功日志并沿线程安全、对象生命周期和启动时序方向排查，不要先清缓存或重建覆盖现场。固定 `2/12 + 0xAD164D0` 仍走 Steam 冷启动预热；稳定资源错误仍走 PAZ/PAMT/PATHC/压缩尺寸排查。
- 详细复盘位于 `T:\C++\vfsDmoe\docs\CrimsonDesert-VFS-随机启动失败线程安全复盘.md`。

### 2026-07-16 坏模组崩溃后的固定 VFS 路径复用排查

- 真实时间线已确认：同一开机周期内，稳定 VFS 在 03:43 前可连续进入；加载坏资源模组后从 04:01 起连续多次固定停在 `(2/12) + 0xAD164D0`。删除坏模组并恢复此前实机成功过的分包字节后，纯 Steam 启动仍可达到 `(12/12)`，但只要重新使用固定 `.cdloader/vfs_active/...` 源路径就继续崩溃；重启后同一指纹、同一映射和同一 runtime 立即恢复。
- 已排除简单等待时长、Steam `SessionUid`、`CharacterCreatorHead.asi` 导致的 crashpad 噪声和分包字节损坏。当前根因方向是坏构建/崩溃后，固定 VFS 绝对路径被保护层、文件对象缓存或残留句柄继续关联；纯净启动不访问该路径，因此无法恢复。
- VFS state schema 6 起，每次冷构建必须在 `.cdloader/vfs_active/snapshot-<fingerprint>-<token>` 下创建唯一不可变快照，全部 PAZ/PAMT/meta 写完后再让 mapping 指向该快照。热启动继续复用 state 中的当前快照；旧快照只做限量、失败可忽略的回收，禁止因删除占用失败阻断启动。
- 该修复已完成同一开机周期的受控实机验证：稳定指纹 `413505...` 的基线快照正常进入存档；启用坏 cloak 模组后切到独立指纹 `65c206...`，游戏达到 `(12/12)` 并完成 SaveSlot 加载后出现 `AppHangB1`；不重启、不预热、不清缓存，仅禁用坏模组后，加载器恢复 `413505...` 字节并创建第三个全新快照路径，随后再次正常进入存档并以 launcher exit code `0` 退出。稳定 PAZ/PAMT 哈希与基线完全一致，证明唯一快照切断了坏路径的后续污染。
- 本地完整测试、真实游戏目录 `BuildOnly`、二阶段热复核、快照结构校验和上述三阶段实机验证均已通过，可以进入正式打包；后续不得回退为直接删除并重建固定 `.cdloader/vfs_active/<package>` 路径。

## 已确认高风险模组记录与红字提示规范

- `services/mod_risk_service.py` 是已确认高风险模组规则的唯一业务入口。以后新增实机确认的危险资源、操作或字节模式时，必须在这里集中登记和匹配，不得把同类判断散落到 scanner、CLI、VFS launcher 或控制台显示层。
- 高风险规则必须由真实游戏证据驱动。至少要记录风险 ID/名称、最终规范游戏路径、危险操作类型或必要字节签名、适用游戏版本、稳定复现的失败阶段、失败与对照成功日志、以及已验证的安全替代方案；对应详细证据写入 `docs/` 经验文档，`AGENTS.md` 保留结论和维护边界。
- 规则必须按最终规范游戏路径、明确操作类型或足够窄的内容签名匹配，不能依赖用户可随意修改的模组文件名。只有取得同类资源的额外实机证据后才能扩大匹配范围。
- 禁止把单个资源的失败结论泛化为整个扩展名、目录或资源大类。例如两个男性飞行披风 prefab 的覆盖失败，不能推导为所有 `.prefab`、PAC、DDS、头部、身体或饰品资源都危险。
- 扫描阶段必须保持轻量：只读取 manifest、组件 JSON、传统 JSON 头部和 loose 文件路径等识别所需元数据，禁止为风险提示解压大型载荷、完整解析 PAZ/PAC 或显著拖慢正常启动。
- 风险业务层只生成统一前缀的告警文本，不直接处理颜色。CMD 亮红色显示统一复用 `utils/console_alert.py`；普通 CLI、打包 VFS 入口和 `run_cdmm_vfs.bat` 开发入口必须路由到同一套提示，不能出现某个入口漏报。
- 每个风险块的正文固定先输出独立一行 `1、【文件或文件夹名称】`，多个实际命中的高风险项按 `2、`、`3、` 连续编号；下一行再输出目标路径、危险操作、实机后果和替代建议，不得把名称与说明重新挤在同一行。
- 高风险提示默认只告知用户，不自动删除、禁用、改写模组或阻止加载。未来若要升级为强制阻断，必须另有明确实机证据、兼容性边界和用户授权，不能直接改变现有非阻断行为。
- 每条新增规则至少要补三类回归测试：危险样本准确命中、路径或后缀相似但未经证实的资源不误报、所有控制台入口复用 CMD 红字路由。修改后至少运行专项 pytest、完整 `test/` pytest 与 `ruff check .`。
- 当前首条已确认规则为男性飞行披风：只有最终目标命中 `character/cd_phm_00_cloak_flight_0001.prefab` 或 `character/cd_phm_00_cloak_flight_0001_index01.prefab` 的危险覆盖操作才报警。失败包 1.1/1.2/1.3 分别复现 `2/12`、存档加载和 `12/12 + End Load SaveSlot` 后崩溃/卡死；已验证替代方案是 PAC 全 LOD 索引退化。

新增风险记录时按以下最小模板维护：

```text
风险 ID / 名称：
适用游戏版本：
最终目标路径：
危险操作或字节签名：
失败阶段与稳定复现次数：
失败日志 / WER / Dump 证据：
对照成功证据：
已验证安全替代方案：
匹配边界与明确不包含项：
回归测试：命中 / 不误报 / 红字入口
```

### 2026-07-16 男性飞行披风完全隐藏实机成功基线

- 换色链已确认由四张男性飞行披风 DDS 控制；鲜红版用户实机确认生效。主体 DDS 透明化只能得到精简披风，无法完全隐藏 `SkinnedMeshWing`。
- 单独修改三个 glide PAAC 无法隐藏黑色披风；`Male Glide Animation` 的主要功能包含 56 个女性到男性动画替换，不能把它当作纯隐藏披风方案。
- 三种 prefab 方案均已实机证伪：复制通用空 prefab、复制同 PHM 域同反射布局空 prefab、保持原结构只把四个 `SkinnedMeshComponent::_isEnable` 从 `1` 改为 `0`。第三种方案只改四字节，仍在 `End Load SaveSlot105` 同一秒 `[Crash]`，证明问题是这两个飞行披风 prefab 被覆盖本身不安全，不是空资源选择或布尔偏移错误。
- 最终成功包 `Meshless Flight Cloak - Native FX 1.0` 完全不修改 prefab、PAA、PAAC、PAC_XML。它校验两个原版 PAC SHA-256，逐段解压内部 PAR/LZ4 几何数据，用 Section 0 的解压后虚拟 split offset 验证索引区，把全部 LOD 的 138,153 个三角形 `(a,b,c)` 退化为 `(a,a,a)`，再按原段压缩策略重建。
- 用户已实机确认最终包可以正常进入存档并实现披风网格完全隐藏；原生动画和 FX 链保持。后续“保留附件生命周期/FX，只隐藏实体”应优先沿 PAC 子网格/索引方向处理，不得再优先尝试删 prefab、换空 prefab、关组件或改 prefab PAC 引用。
- 加载器只在 `.cdmod resource-transform/file-replacement`、传统 JSON byte patch 或 loose files 的最终目标命中 `character/cd_phm_00_cloak_flight_0001.prefab` 或 `character/cd_phm_00_cloak_flight_0001_index01.prefab` 时，复用 `utils/console_alert.py` 输出 CMD 亮红色告警。这两个路径有 1.1/1.2/1.3 三轮实机失败证据；不得泛化到头部、身体、饰品等其他 prefab，也不得自动禁用或阻止加载。
- 完整经验位于 `docs/CrimsonDesert-男性飞行披风隐藏与换色模组制作经验.md`；生成工具为 `tools/build_meshless_flight_cloak_mod.py`。

### 2026-07-12 Crimson Desert VFS 启动方式最终稳定基线

- 2026-07-12 重启冷启动补充：用户复现电脑重启后直接 VFS 启动连续两次均在数据加载 `2/12` 以固定 `CrimsonDesert.exe+0xAD164D0 / 0xc0000005` 崩溃；07:56 经 Steam 原生启动成功加载到 `12/12` 后，07:57 同一套 28 文件 / 14 目录 VFS 立即成功。这不是 `.cdmod` 或映射内容变化，而是 Steam 冷启动会话/保护层初始化要求。
- 已证伪“只要改成 Steam URI 就能解决冷启动”：用户重启后实测，`steam://run/<AppId>` 确实让 Steam 以 `PlatformServiceType=Steam` 创建游戏，sidecar、28/14 映射和 Hook 也全部成功，但只要本次开机第一场同时加载 VFS ASI，游戏仍在 `2/12` 固定 `+0xAD164D0` 崩溃。纯 Steam 启动成功后再加载 VFS 才稳定，因此必须分成两个独立进程阶段。
- 当前默认流程按 Windows 开机会话和最近启动时间线判断预热状态：若本次开机没有 `.cdloader/steam_warmup_boot.marker`，也没有本次开机后达到 `(12/12)` 的游戏日志，加载器先清理自有临时 ASI，通过 Steam 纯净启动游戏；用户进入主菜单并正常退出后，加载器写入本次开机标记并自动继续 VFS 启动。同一开机周期通常不重复预热，但若后续游戏日志停在 `(2/12)`，且 WER 同时确认 `CrimsonDesert.exe / 0xc0000005 / +0xAD164D0`，则更早的 marker 和 `(12/12)` 必须立即失效，下一次启动自动执行恢复性纯 Steam 预热；只有该崩溃之后新的 `(12/12)` 或新 marker 才能恢复有效状态。
- 2026-07-12 用户已完成真正冷启动实机验证：重启后运行加载器，第一阶段纯 Steam 游戏可正常进入；用户退出后第二阶段 VFS 自动启动成功，确认两阶段方案有效。
- Steam 预热只能用于 Steam 发行版。自动识别必须同时满足：目标路径属于某个 `steamapps/common/<installdir>`，且对应 `appmanifest_*.acf` 的 `installdir` 与目标游戏根目录匹配。非 Steam 版或无法匹配 manifest 时，必须保持 AppID 为空、跳过 Steam URI/预热，按平台无关的 ASI 直接启动；禁止按游戏名硬编码 `3321460` 或给其他发行版伪造 Steam 环境变量。显式 `-SteamAppId` 仅作为用户/开发者主动覆盖。
- VFS 阶段仍使用：物化 `nppvfs_runtime_<PID>.asi` 和同名 `.asi.env` -> `steam://run/<AppId>` -> 按完整 EXE 路径识别游戏进程 -> ASI Loader 加载 runtime。Steam 进程不会继承 cdloader 环境变量，因此 sidecar 必须携带 VFS 配置，runtime 在 loader lock 外读取后再安装 Hook。
- 启动前和游戏退出后必须清理自有临时 `.asi` 与 `.asi.env`；没有 Steam AppID 时保留原 ASI 直接启动，`-UseRemoteInjection` 仍只作为诊断回退。后续不得删掉 sidecar 后只保留 Steam URI，否则会出现“游戏能进入但所有 VFS 模组不生效”。
- 合成验证已覆盖 sidecar 独立自动安装：父进程不提供 VFS 环境时，临时 ASI 从 sidecar 恢复配置并记录 `InstallVfsHook result: TRUE`。两阶段预热流程最终仍需用户实机确认。

- 用户已实机确认：同一套 28 文件 / 14 目录 VFS 映射，旧的“创建挂起进程后远程 `LoadLibrary` runtime、安装 Hook、再恢复主线程”方式会稳定在游戏数据加载 `2/12` 时以 `0xc0000005` 崩溃，故障偏移固定为 `CrimsonDesert.exe+0xAD164D0`。Steam 预热、恢复旧 VFS 快照、替换新旧 `.cdmod` 产物都不能改变该偏移，因此这不是 `.cdmod`、PAZ/PAMT、PAPGT 或单个模组数据损坏。
- 完整 dump `C:\Users\liuho\AppData\Local\CrashDumps\CrimsonDesert.exe.17944.dmp` 显示故障指令位于游戏保护/虚拟化指令序列，执行 `add ebp, dword ptr [r13]` 时 `r13` 为无效地址。该证据只能用于合法兼容性排障，禁止尝试绕过或削弱游戏保护。
- 最终修复是改变加载拓扑，不是减少模组内容：Crimson Desert 默认使用 `Release|x64` runtime，由游戏目录现有的 Ultimate ASI Loader 正常加载临时 `nppvfs_runtime_<PID>.asi`；runtime 的 `DllMain` 只创建 worker，实际配置读取与 Hook 安装在 loader lock 外执行。游戏退出后 launcher 自动删除该临时 ASI，下次启动也会清理同前缀残留。
- 用户使用原命令 `run_cdmm_vfs.bat -AllowMissingTargets -NoBuildVfsDemo -KeepRunning` 已确认可完美进入游戏。源码入口 `run_cdmm_vfs.ps1` 默认传 `-Configuration Release -AsiLoad`；打包入口 `tools/vfs_launcher.py` 默认给原生 launcher 追加 `--asi-load`。两条入口必须保持一致。
- 旧远程注入只保留为诊断回退：源码入口显式传 `-UseRemoteInjection`，打包入口显式传同名参数时才允许恢复旧方式。后续不得把远程注入重新设为 Crimson Desert 默认，也不要因为通用 smoke test 通过就回退该稳定基线。
- 正常 ASI 模式 launcher 日志应包含 `VFS runtime will be loaded by the game's ASI Loader.` 和 `ASI runtime materialized:`；runtime 日志仍应包含 `InstallVfsHook called.`、`mapping manifest loaded files=... directories=...`、`InstallVfsHook result: TRUE`。如果前两行缺失，先检查入口是否错误回到了远程注入模式。
- 本次失败现场已冻结在 `G:\SteamLibrary\steamapps\common\Crimson Desert\.cdloader\vfs_crash_reference_20260712-025328`，包含约 623 MB 的 `vfs_active`、状态/映射、VFS 日志和游戏日志；后续需要复盘固定偏移时优先使用该快照，不要用新构建覆盖后的现场倒推。

### 2026-07-12 `.cdmod` 首次游戏内正式验证

- `Equip Everything V6.json` 已从游戏 `mods` 卸载，目录中只保留 `Equip Everything V6.cdmod`。不能把旧 JSON 与 `.cdmod` 同时存在的结果算作新格式验证。
- 为排除旧产物和分包缓存，测试前明确删除 `.cdloader/vfs_state.json` 与 `.cdloader/vfs_package_cache`（当时共 42 个缓存文件），但保留 `.cdloader/vanilla`、`.cdloader/load_order.json` 和失败快照；随后用 `run_cdmm_vfs.bat -AllowMissingTargets -NoBuildVfsDemo -KeepRunning` 完整冷构建。
- 冷构建后的 `vfs_state.json` 明确包含 `Equip Everything V6.cdmod`，最终生成 `nppv3_equipslotinfo` 与 `nppv3_iteminfo`，并通过 Release ASI VFS 稳定进入游戏。用户已确认 Equip Everything 功能在游戏内正常生效，因此 `.cdmod` 不能再标记为“仅字节等价、尚未实测”。
- 该结果同时验证了当前完整链路：`.cdmod ZIP -> 严格解析 -> 动态选择器展开 -> 多模组语义计划 -> Format 3 writer 分流 -> PABGB/PABGH -> DMM-like PAZ/PAMT -> PAPGT -> VFS ASI`。后续修改其中任一层，至少要保留 `Equip Everything V6.cdmod` 的单包字节等价回归和真实冷构建生效基线。
- 当前实测范围仍是语义型 `.cdmod`；资源替换、DDS、standalone PAZ/PAMT 等内容是否封装进 `.cdmod` 尚未实现或实测，不能据此宣称新容器已覆盖所有旧模组形态。

### 2026-07-12 PALOC 本地化 `.cdmod` 首次游戏内正式验证

- 真实样本为 Nexus `Display take and steal price 1.0.8`。旧模组携带完整
  `localizationstring_zho-cn.paloc`，大小 `15,288,641` 字节、179,571 条记录；
  当前游戏原版为 16,052,857 字节、187,526 条记录。旧整表相对当前原版有
  3,784 条文本差异、2,590 个旧表独有 key，并缺少当前原版 10,545 个 key，
  因此不能把整表差异全部转换，否则会把大量旧翻译和旧版本结构带回新游戏。
- 新转换器只提取 24 个真实功能 key，把意图表达为语言无关操作：
  `target=gamedata/localizationstring_*.paloc`、`op=append`、
  `suffix=" ({price})"`。`.cdmod` 不携带完整中文/英文 PALOC；VFS 构建时读取
  当前游戏语言对应的最新原版 PALOC，保留该语言原文，仅对这些 key 追加价格。
- 游戏原版 `0032/0.paz + 0.pamt` 实际包含 14 张平行 PALOC，例如 `kor`、
  `eng`、`jpn`、`zho-tw`、`zho-cn`。通配包不得一次重建全部语言表，否则会
  生成约 224 MB 无用 overlay。加载器只重建活动语言的一张表；语言选择优先
  `CDLOADER_LANGUAGE` 显式覆盖，再读取与游戏目录匹配的 Steam appmanifest
  `language`，最后才按系统区域回退。活动语言必须写入 `vfs_state.json` 并参与
  整包缓存复用判断，切换语言后必须冷重建，禁止复用上一语言的 VFS 成品。
- 正式测试包位于游戏 `mods/Display take and steal price-1.0.8.cdmod`，大小
  `1,576` 字节，SHA-256 为
  `8b77fbd2476b0ac516361e6576caeaa37e461dad69aaa91c99929c529488bf3b`。
  用户使用原 VFS 命令进入游戏后已确认“拿取/偷窃显示价格”功能正常生效。
  这证明 `.cdmod` PALOC 链路不仅显著减小分发体积，还避免旧完整语言表覆盖
  游戏更新新增记录。后续不得把该能力描述为“只完成转换、尚未实机验证”。
- 当前已验证的是已有 PALOC key 的 `set` 与幂等 `append`。新增/删除 PALOC
  记录规则尚未确认；不得为追求通用性盲目插入记录。语言无关 `append` 应保留
  每种语言原文；自定义 `set` 文本应按语言分别提供，禁止把中文常量写入所有语言。
- GitHub 正式发布 `.cdmod` 格式时，必须同步发布面向普通玩家和模组作者的详细
  使用教程。教程基线记录在 `docs/cdmod格式GitHub发布与使用教程.md`，必须说明
  安装、排序、禁用、冲突/更新提示、转换流程、格式优势、游戏更新适配原理、
  已支持组件和未支持边界。不得只发布格式 JSON 示例而没有可操作教程。

### 已验证有效的修复和经验

### 2026-07-12 全量 mods 批量转换为 `.cdmod`

- 新增 `convert_all_mods_to_cdmod.ps1` 与纯英文 BAT 包装，可一次扫描整个 `mods`
  并输出确定性 `.cdmod` 和 `conversion-report.json`。PAMT 目标必须先全量注册，再串行
  解析 root loose；不能让多个线程各自增量扫描全游戏 PAMT，否则会重复高 CPU 全表扫描。
- 当前容器已覆盖并接入真实运行链：Format 3、传统 JSON byte patch、`files/NNNN`、
  根部 `NNNN`、`files/<game-path>`、根游戏路径 loose、完整资源、新增资源、PALOC、
  resource-transform，以及 standalone PAZ/PAMT。standalone 组件只携带校验后的 PAZ/PAMT，
  仍由加载器分配安全目录并统一重建 PAPGT，禁止采用包内旧 meta。
- 传统 JSON 使用 `legacy-byte-patch` 原语组件，运行时复用现有 JSON patch 合成器；不得
  重写 offset 重定位、重复 offset、半应用和 PABGH 行为。旧 loose 完整表替换只有转换器
  显式写入 `allow_table_replace` 时才允许，普通 file-replacement 仍必须禁止覆盖表/meta/归档。
- 新增资源使用 `allow_new`，当原版没有 basename 时优先从同目录 sibling 推断 PAMT 和
  最终父路径；整目录全新增只允许按稳定顶层分区回退（当前 `character=0009`、`ui=0012`）。
  新 entry 必须设置 `preserve_entry_dir`，否则 vanilla flattened-path map 可能把声明的
  `character/texture` 错误恢复成其他目录。
- 真实游戏集合已完成56个旧模组转换，加上5个先前已有包，游戏 `mods` 当前只保留61个
  `.cdmod`。旧文件和目录未删除，已移动到
  `G:/SteamLibrary/steamapps/common/Crimson Desert/mods-original-backup-20260712-1630`。
  纯 `.cdmod` BuildOnly 成功：总耗时约22.78秒，2479个完整资源 entry、7个 standalone、
  PATHC更新350条并新增40条、跳过0条；VFS 映射和 `vfs_active` 已成功生成。
- `VirtuousTreaty` 旧 Workbench 元数据错误声明新纹理 `cd_phm_02_handle_0014.dds`，实际
  文件与 `.pac_xml` 均引用 `cd_phm_02_handle_0036*.dds`，旧版游戏内表现为武器材质错误。
  新 `.cdmod` 按真实 `.pac_xml` 路径把4张0036纹理作为0009新增 entry，并注册
  `character/texture/...` PATHC；最终 PAMT folder record 已确认是 `character/texture`。
  该修复已通过构建级验证，但视觉效果仍需用户进游戏确认，未确认前不得写成实机修复。

### 2026-07-12 Equip Everything / Electro Mecha `.cdmod` 组合回归

- 全量转换后用户实机发现：`Equip Everything V6.cdmod` 不生效，
  `Electro-Mecha_Longsword_to_Lightsaber.cdmod` 装备时武器不显示并导致游戏崩溃。
  Equip 包与首次生效的 `prototype_output/Equip Everything V6.cdmod` SHA256 完全一致，
  因此故障不在容器转换字节，而在多包合成顺序和 writer 分流。
- `.cdmod file-replacement` 必须与旧 loose 一样在传统 JSON / Format 3 前作为 base；不得
  在 Format 3 后写完整 `iteminfo.pabgb`，否则会覆盖所有语义修改。当前顺序已修正为
  `loose + cdmod完整资源 base -> JSON -> Format 3 -> localization/resource-transform`。
- Electro 的真实字段是同一 key `1001062 / Marni_MachineKnight_TwoHandSword` 的完整
  `prefab_data_list` 与 `gimmick_visual_prefab_data_list`。两者必须成对写入；只写 gimmick
  会产生隐形武器/崩溃风险。native parser 已有 GimmickVisualPrefabData schema，因此新增
  单记录 visual 双列表 writer，不允许因此把全部 iteminfo intent 拖入高内存 whole-table。
- cdmod Format 3 bridge 必须把拥有同 selector gimmick visual 的普通 prefab 一起路由到
  `iteminfo-visual-prefab`，先处理 whole/drop 等基础字段，最后处理 prefab/visual 字段。
  DMM transmog 在目标 prefab 原列表为空时会省略 `tag_name_hash`；仅在同 selector 同时存在
  gimmick visual 的窄路径中补 `tag_name_hash=0`，不得全局放宽空 prefab 安全检查。
- 修复后纯 `.cdmod` 冷构建约25.11秒，生成3909个补丁。最终 key 1001062 的两个列表
  均包含 prefab hash `1431011488`；Equip Everything 原 JSON与cdmod单包四个输出
  PABGB/PABGH SHA256完全一致，当前多模组最终表抽样26条 legacy prefab 块全部存在。
- `FAT STACK ALL IN ONE.cdmod` 携带旧完整 iteminfo 且无配套 PABGH，已写入
  `.cdloader/disabled_mods.json` 禁用但未删除。不要用单元素 `ConvertTo-Json` 直接写禁用
  文件，否则会生成JSON字符串而不是数组并被scanner忽略；当前文件必须保持字符串数组。
- 上述为构建级与字节级修复；Electro不再崩溃和Equip功能恢复仍需用户再次进游戏确认，
  未收到实机确认前不得记录为正式游戏内验证。

#### 实机结论修正

- 2026-07-12 17:00 用户实机确认上述全 `.cdmod` 产物无法进入游戏。最新 Launcher 日志
  连续报告 `ItemInfo의 _key를 읽어들이는데 실패했다`，随后数据加载 `(3/12)` 失败并退出。
  因此前述“Electro两个列表均写入”只能算结构观察，不能算合法游戏记录；不得继续宣称已修复。
- 已立即恢复转换前 mods 与原 `load_order.json`，56个批量产物移至
  `mods-cdmod-failed-20260712-1700`，原件仍在 `mods-original-backup-20260712-1630`。
  当前显式禁用原本就不生效的 `Electro-Mecha_Longsword_to_Lightsaber.json`。
- 回滚重建后的 `nppv3_iteminfo/0.paz` SHA256 为
  `2c08bd82bfbae0f642d52df01ac2626ff9d43ec6d9a6bca275f01cd9c3e93975`，与
  `.cdloader2` 当前、trial backup、crash reference 和 package cache 四份历史稳定快照完全一致。
  后续批量转换必须以该哈希/对应表字节为稳定 oracle，逐类接入并实机验证；禁止再次一次性替换
  全部 mods。Electro 没有 DMM 成功产物前保持禁用，不得凭字段 JSON 结构猜测合法序列化。
- 用户随后实机确认回滚基线可正常进入且模组生效。排除 Electro 后重新部署60个 `.cdmod`，
  冷构建 `nppv3_iteminfo/0.paz` 仍与上述稳定哈希完全一致；该60包集合实机也已确认正常。
  Electro 当前没有安装/生效是明确隔离策略，不得向用户描述成转换完成。
- 60包首次冷构建曾约23.92秒，原因是同一个 `.cdmod` 在scanner、目标收集、file base、
  JSON、semantic和standalone阶段被重复打开、解压、SHA校验。`load_cdmod_package()` 现按
  `绝对路径 + mtime_ns + size` 做进程内严格解析缓存，文件变化自然失效。优化后同条件冷构建
  11.89秒（scan 1.78s、目标统计0.04s、file base 0.45s、JSON 0.11s、standalone 0.01s），
  未改模组的整包热构建1.78秒，已快于转换前约14.40秒冷构建基线。
- 2026-07-12 Electro 加入后的61包首次全冷构建一度回退到60.10秒；分段日志证明并非
  `.cdmod` 解析变慢，而是目标预筛选后，overlay 打包又为WEM完整解析大型原版PAMT：
  loose阶段13.99秒、`nppvoice` 32.37秒。`PazEntry` 现从PAMT folder record携带
  `resolved_dir_path`，并经 `OverlayInputEntry` 传到打包器；已有最终目录时禁止二次全表扫描。
  `pamt_target_cache.json` schema已升至3，避免旧缓存缺失目录字段。相同61包强制冷构建降至
  14.28秒，其中loose阶段2.02秒、`nppvoice` 0.41秒；优化前后语音PAZ/PAMT的SHA256及长度
  完全一致。后续不得删除该目录传递链路，也不能为WEM恢复逐entry/逐包全PAMT回扫。
- 后续严格按“`vfs_state.json`、`pamt_target_cache.json`、`vfs_package_cache` 全部不存在”
  测首次安装性能，不能用整包热命中冒充冷构建优势。分包缓存与 `vfs_active` 位于同一NTFS
  卷时，首次构建只写一次PAZ/PAMT，再用硬链接建立缓存；缓存命中也直接硬链接物化，禁止把
  约548MB的 `nppsa+nppvoice` 重复写两份。该改动后严格全冷为12.97秒，`nppsa` 3.37秒。
- `.cdmod` 扫描现只校验manifest和组件索引，严格payload SHA256仍在实际消费前执行；PAMT
  目标可先从轻量组件JSON收集，使大载荷解压与目标预筛选具备并行基础。当前61包严格全冷
  实测13.17秒，输出仍为3909个Format 3补丁、254个跳过；`nppv3_iteminfo`、`nppvoice`、
  `nppsa` 的PAZ/PAMT与优化前SHA256完全一致。13秒只算阶段性结果，尚未形成相对旧格式
  约14秒的明显优势；下一优先级是 `_cdloader_native` 目标过滤PAMT解析和流式PAZ落盘。
- 曾尝试把1166次动态insert的PABGH修正合并到最后一次执行，并用 `bytes.find` 替换legacy
  prefab尾部逐字节定位；合成测试虽通过，但真实全量只应用1453/1483并把补丁数降到2860，
  已撤销。不得再次在没有3909/254真实冷构建回归和游戏实测的情况下批处理该动态顺序。
- 2026-07-12 严格全冷性能已压到9.99秒（61个 `.cdmod`，同时删除/移走
  `vfs_state.json`、`pamt_target_cache.json`、`vfs_package_cache` 后重建）。关键优化为：
  `_cdloader_native.parse_pamt_filtered` 在C++中完成目标PAMT过滤并释放GIL，Python保留排序、
  缓存和 `PazEntry` 规则；`_cdloader_native.fixup_pabgh_offsets` 原生执行每一次PABGH指针
  修正，但绝不改变1166次动态insert的调用顺序；大型PAZ直接以bytearray落盘，避免483MB
  `bytearray -> bytes` 复制；legacy prefab候选用 `bytes.find` 定位scale特征后再走完整结构校验。
- 9.99秒基线仍输出3909个Format 3补丁、跳过254个intent；`nppv3_iteminfo`、`nppvoice`、
  `nppsa` 的PAZ/PAMT SHA256与优化前完全一致。CPython 3.10和3.12原生扩展均已重建。
  原生接口缺失时必须继续使用Python fallback；不得把原生模块变成加载正确性的硬依赖。

#### Electro Mecha 最终实机修复结论

- 2026-07-12 用户实机确认最终版 `Electro-Mecha_Longsword_to_Lightsaber.cdmod` 已生效：
  游戏可以正常通过数据加载并进入，目标武器可正确显示为光剑，装备时不再崩溃。
- 此前把 `prefab_data_list` 与 `gimmick_visual_prefab_data_list` 按推测 schema 重新序列化的
  方案是错误方案，会破坏 ItemInfo 记录边界并触发 `(3/12)` 数据加载失败。不得恢复该方案，
  也不能仅以“解析后两个列表均含目标 hash”作为合法性依据。
- 最终正确方案是在稳定 ItemInfo 中按唯一 prefab hash `1431011488` 动态反查合法源记录
  `1000267 / LightSaber_TwoHandSword`，再把其完整 legacy visual 尾部块复制到目标记录
  `1001062 / Marni_MachineKnight_TwoHandSword`。该匹配基于游戏数据中的唯一 prefab hash，
  不能按 Electro 模组名或固定文件路径硬编码。
- 当前源块和目标块均为74字节，目标写入后与源块字节一致，prefab hash 在块内出现2次；
  最终 PABGH 的6508个 key 全部与 PABGB 对齐。后续同类武器外观/光剑替换应优先复用
  `format3_iteminfo_writer` 的 legacy visual 完整块复制能力，不能猜测未完全掌握的原生字段布局。
- 这次实测把 `.cdmod` 的 ItemInfo 支持范围推进到“复用游戏已有合法视觉记录”的武器外观替换；
  它不代表任意全新 visual 结构都已可安全生成。遇到没有合法游戏内源记录的模组，仍需先解析
  完整二进制布局并进行 PABGB/PABGH 结构验证，再安排实机测试。

#### 4xAtkSpd / No Fall Damage Format 3 首次游戏内正式验证

- 2026-07-12 用户报告 `4xAtkSpd.cdmod` 与 `No-Fall-Damage.field.json` 均无效果。
  原因不是容器或安装链路：4x的16条 `statusinfo.stat_level_data[N]` 因没有writer全部跳过；
  No Fall 依赖 `buffinfo.buff_data_list[9].data.variant.body.f01` 和
  `iteminfo.enchant_data_list[N].equip_buffs`，后者未支持，且live游戏tag104已从旧9字节
  变成17字节，旧walker从第2级开始错位。
- 新增通用 `format3_statusinfo_writer.py`：唯一定位 `u32 count + u64[count]`，同一entry
  用最大intent索引确定数组起点。4x最终16级值为
  `0, 116000000, ..., 1000000000`，生成独立 `nppv3_statusinfo` 包；第0级原本就是0，
  因此16个intent中15个产生实际字节变化、1个记为already expected。
- live tag104已由FallDamageReduce连续10级原版记录确认是
  `f00:u8 + f01:u64 + f02:u64`，尾部17字节。不得恢复旧9字节步长。
- ItemInfo新增窄支持 `enchant_data_list[N].equip_buffs`：当前live EnchantData在旧结构后
  多一个尾部u32。writer按 `count`、连续level `0..N-1`、合法旧结构+尾u32定位候选，
  多候选时只接受唯一最大合法跨度；同一entry的全部equip_buffs先合成完整EnchantData数组，
  再输出一次动态替换，避免多个空数组insert发生模式歧义。该字段必须走独立
  `iteminfo-enchant-equip-buffs` bridge family，禁止混入whole-table批次。
- `No Fall Damage-4.4.cdmod` 已安装到游戏mods，55条语义操作完整保留。全量组合构建为
  10个Format 3目标、3948个生成补丁、239个跳过，无No Fall独立跳过/错误；最终buffinfo
  两个目标值分别是100000(u32)与100000000000(u64)，Miner头盔11级和各目标灯笼均写入
  `buff=1000185, level=10`。最终ItemInfo的6508条PABGH offset与6508条记录一致。
- 2026-07-12 用户随后实机确认两个模组均成功加载并生效：4xAtkSpd攻击速度效果正常，
  No Fall Damage减伤链正常，游戏数据加载与进入游戏稳定。至此 `statusinfo` 等级数组、
  `buffinfo` live variant和 `iteminfo` live EnchantData窄写入正式从构建级升级为游戏内验证。
- 后续修改status/buff/enchant任一writer，必须保留以下回归锚点：4x的16级最终u64数组、
  No Fall两个buff目标值、Miner头盔11级equip_buffs、目标灯笼equip_buffs、6508条
  ItemInfo PABGH/PABGB一致性，以及全量组合可正常进入游戏。
- 上述 `tag104` 是 2026-07-12 旧版游戏表基线，不得外推到后续版本。1.17 已确认
  `ChangeBuffLevelBuffData 80 -> 79`、`AddPercentInGameContentsBuffData 104 -> 103`；
  当前迁移与实机结论见本文件“2026-08-10 No Fall Damage / BuffInfo 1.17 迁移实机基线”。

#### Direct Attack Speed 无齿轮直接生效与多倍率制作基线（2026-07-16）

- `4xAtkSpd` 原包只修改 `gamedata/statusinfo.pabgb` 的 `AttackSpeedRate`
  （key `1000010`）记录，字段为 `stat_level_data[0-15]`。原包第0级仍写 `0`，其余等级写
  `116000000 ... 1000000000`；角色未装备任何增加攻速的装备时停在第0级，因此模组看似
  “无效果”，装备一件增加攻速的齿轮/装备后进入更高等级才开始生效。这不是VFS、容器或
  writer失效，而是原始语义数据本身保留了零级条件。
- 无装备直接生效不能只把 `stat_level_data[0]` 改成高值，否则角色之后进入1～15级时会被
  原曲线的较低值覆盖，表现为装备攻速物品后反而降速。稳定做法是把0～15级全部固定为同一个
  目标值；这样无装备时立即生效，切换或装备攻速物品也不会改变所选档位。
- 原 `4xAtkSpd` 的最高档 `1000000000` 对应原版最高档 `250000000` 的4倍。沿用该模组的
  `AttackSpeedRate` 曲线倍率命名口径，多倍率直接生效值固定为：x2=`500000000`、
  x3=`750000000`、x4=`1000000000`、x5=`1250000000`、x10=`2500000000`。
  这些名称表示对该状态曲线的倍率预设；引擎最终动画/动作速度还可能受自身公式、上限和动作
  时序影响，不得宣称已经仪器测得实际播放速度严格等于数学倍数。
- 2026-07-16 用户实机确认：将 `AttackSpeedRate` 的0～15级全部固定为 `1000000000` 后，
  4倍档无需装备攻速齿轮即可直接生效。x2/x3/x5/x10按同一规则线性生成，并分别通过真实原版
  StatusInfo writer构建；最终PABGB中 key `1000010` 的 `u32 count` 为16，后续16个u64均与
  对应档位值一致，companion PABGH可正常解析。除x4外的其他档位目前只有容器与构建级验证，
  尚未分别记录游戏内实测。
- 五个正式包统一使用版本 `1.13.01`、模组ID `direct-attack-speed`，以便玩家误装多个倍率时
  由语义计划拒绝重复ID，而不是静默叠加同一字段。每个Nexus文件必须是独立ZIP，ZIP根部只含
  对应的一个 `.cdmod`；安装文案必须强调五选一。高倍率可能让部分动画或动作时序显得异常，
  温和使用优先推荐x2或x3。
- 游戏当前启用文件已改名为 `Direct Attack Speed x4-1.13.01.cdmod`，并在
  `.cdloader/load_order.json` 原位置替换旧 `4xAtkSpd.cdmod` 名称，不能因为改名把优先级
  自动挪到末尾。Nexus资料位于 `nexusmods/18-direct-attack-speed-1.13.01-cdmod`，包含
  x2/x3/x4/x5/x10五个独立ZIP、双语BBCode、短描述、SHA-256和1600x900黑底双语封面；
  在获得实际Nexus页面URL前只能表述为“发布资料已准备”，不得宣称已经上线。

#### Direct Movement Speed 4x 直接生效与多倍率发布基线（2026-07-16）

- 来源组合包 `2xAtkSpd_2xMovSpd.json` 同样只修改 `statusinfo.pabgb`；移动速度记录为
  `MoveSpeedRate / key 1000011`，字段仍是 `stat_level_data[0-15]`。其2倍曲线为
  `0, 40000000, 80000000, ..., 500000000`，最高档反推原版为 `250000000`。
- 独立4倍直接生效包只提取 `MoveSpeedRate` 的16条操作，不携带 `AttackSpeedRate`；稳定值与
  攻速包相同，0～15级全部固定为 `1000000000`，避免无移速装备时停在零级，也避免装备变化
  后切回较低档。正式测试文件为游戏 `mods/Direct Movement Speed x4-1.13.01.cdmod`，版本
  `1.13.01`、模组ID `direct-movement-speed`、SHA-256
  `37F267E787325C1AF9E3D013792DD23881C77BF3E96C0C64A22484565DBA341D`。
- 2026-07-16 构建时玩家当前实际启用的是 `Direct Attack Speed x10-1.13.01.cdmod`。
  x10攻速与x4移速联合语义计划无警告、无错误，最终同一StatusInfo中
  `AttackSpeedRate / key 1000010` 的16个u64均为 `2500000000`，
  `MoveSpeedRate / key 1000011` 的16个u64均为 `1000000000`，PABGH可正常解析；这证明两包
  修改不同记录，可以在一个最终表内合成。用户随后实机确认x4移速无需移速装备即可直接生效，
  因此 `MoveSpeedRate` 全等级固定策略已从构建级升级为游戏内验证。
- 多倍率发布沿用原版最高档 `250000000`：x2=`500000000`、x3=`750000000`、
  x4=`1000000000`、x5=`1250000000`、x10=`2500000000`。发布目录固定为
  `nexusmods/19-direct-movement-speed-1.13.01-cdmod`；五档均为独立ZIP，x4必须保持上述实机
  源包字节与SHA-256完全不变，其他四档已完成真实StatusInfo writer与PABGH构建验证但尚未分别
  实机。新版Nexus安装文案必须从Steam“管理 -> 浏览本地文件”开始，并包含完整目录树、ZIP只
  解压一次、`.cdmod`不解压、`.cdmod.123`停用、VFS整包放置、首次Steam预热和小白快速检查。

#### 从本轮形成的底层加载认识

- “模组被扫描到”不等于“模组生效”。必须沿
  `容器解析 -> 能力声明 -> writer分发 -> 生成byte change -> PABGH修正 -> 分包 -> PAPGT -> VFS`
  逐层确认。出现“无效果但不崩溃”时，第一优先看目标writer的生成/跳过数量，而不是重新打包容器。
- Format 3字段名只是作者意图，不是二进制布局证明。没有DMM产物时仍可通过PABGH记录边界、
  同类记录重复结构、数组count、连续level/key、字段数值关系和当前游戏原版字节反推；只有候选
  唯一且边界自洽时才允许写入。DMM可以作为oracle，但绝不能成为实现新类型的必要依赖。
- 游戏更新失效通常发生在“结构版本漂移”而不是路径本身。例如tag104从9字节增长到17字节、
  live EnchantData新增尾部u32。writer必须验证当前记录结构并动态定位；旧固定offset或旧schema
  即使仍能解析第一条，也可能从第二条开始错位并静默无效。
- 跨表模组必须按完整依赖链处理。No Fall不是单独改buff数值：`buffinfo`定义减伤效果，
  `iteminfo.enchant_data_list[N].equip_buffs`负责把Buff挂到装备；只成功一张表仍然无效果。
- 同一记录多个变长intent必须先内存合成成一个整段替换。对11个相同空equip_buffs逐条insert
  会产生模式歧义；按entry合成完整EnchantData数组后只做一次动态替换，才能稳定修PABGH。
- bridge family是正确性边界，不只是性能分类。窄字段若混入whole-table批次，可能被旧保护
  规则跳过或触发昂贵整表解析；新窄writer必须有独立family，并明确安排在最终加载顺序中。
- `.cdmod`性能优势必须按三层缓存全空的严格冷构建衡量。已验证61包基线从60.10秒降到9.99秒，
  且关键PAZ/PAMT字节不变；核心是原生目标PAMT过滤、原生PABGH指针修正、NTFS硬链接缓存、
  避免大型PAZ重复复制，以及轻量容器索引。热构建约1.7秒只能作为日常启动指标，不能冒充冷构建。
- 新类型的标准接入顺序应固定为：先在真实模组上统计目标/字段/跳过原因；再从当前游戏原版
  唯一定位记录结构；实现表级窄writer和能力规则；安排独立bridge family；验证PABGB/PABGH、
  最终分包与加载顺序；最后实机验证效果和稳定性。禁止按模组名硬编码，也禁止只看测试通过
  就宣称游戏内生效。

### 2026-07-12 `.cdmod` 头部资源动态重定向首次游戏内验证

- 真实样本为 `K-Makeup for Cordelia`。原妆容包只替换
  `cd_phw_00_head_base_youth_0027.dds` 与对应 `_n.dds`，自身不负责让角色使用
  0027 头部；原包必须与重定向包同时保留，不能因新增重定向包而删除。
- Human Female 角色创建 XML 已确认：界面第一张女性脸是 XML `Index=0`、资源
  `cd_phw_00_head_00_0001`；`Index=1` 是空占位/眉毛项；第 26 号索引才对应
  `cd_phw_00_head_00_0027`。不要把 UI 的“第1张脸”机械解释成 XML `Index=1`。
- 首次探针只用 `resource-transform/copy-entry` 把
  `character/cd_phw_00_head_00_0027.pabc` 动态复制到 `...0001.pabc`，用户实机确认
  完全无效果。该结果证明头部替换不能退化成单独覆盖 `.pabc`。
- 正式生效包把 0027 的 `.hkx`、`.prefab`、`.pabc`、`.pac`、`.pac_xml`、
  `.prefabdata_xml` 六项当前游戏资源动态复制到对应 0001 目标；其中 0027
  `.pac_xml` 继续引用 K-Makeup 覆盖的 `head_base_youth_0027` 材质。用户重新冷构建
  VFS 后确认第一张脸成功变为带 K-Makeup 的 0027 头部。
- 实测包为游戏 `mods/K-Makeup Cordelia 0027 as First Face-1.0.cdmod`，manifest
  版本 `1.0.1`、大小 `1,138` 字节、SHA-256 为
  `5e13524ba03f076826c2f8546fdb54917563ef996008934d7fc5c0bcf8aebf97`。这正式验证了
  `.cdmod ZIP -> resource-transform/copy-entry -> 当前游戏源资源解析 -> 多 entry
  overlay -> VFS` 链路可以在不携带重复大资源的情况下完成角色资源替换。
- 2026-07-16 后续实机边界已细化：`1.0.1` 并不是整张 0027 脸在重新读档后失效，
  头部几何仍然保持；真正丢失的是 K-Makeup 的 diffuse/normal 妆容。用户不需要重启游戏，
  仅返回主菜单重新加载存档即可稳定复现，重新进入理发师选择第一张脸后妆容会暂时恢复。
  当前证据表明读档会按 0001 身份重新请求 `cd_phw_00_head_0001.dds` 与对应 `_n.dds`，
  而 `1.0.1` 只让复制后的 PAC_XML 引用 0027 妆容路径，没有覆盖这两个 0001 回退路径。
- `1.0.2` 实验曾临时让加载器 `copy-entry` 优先读取前序 file-replacement 合成 source；重定向包
  `1.0.2` 在原六项头部链之外，把 K-Makeup 已替换的两张 0027 DDS 动态复制到上述 0001
  diffuse/normal 路径，不重复携带 5.6 MB 贴图。新包 SHA-256 为
  `139f734ce0807e62567c3e6404455109a9de311238c04791700781d500fac4b1`；VFS 成品已验证
  0027 与 0001 两组 PAZ payload 分别字节一致，PATHC 同步更新。用户随后完成详细实机验证：
  `1.0.2` 仍然失败，返回主菜单重新加载存档后妆容照常丢失；直接选择原生第 27 张脸则不会
  丢妆。该结果证明问题是存档/角色系统按 0001 身份重建材质状态，不是缺少某个 0001
  DDS 路径；后续禁止继续沿“把 0027 资源复制/别名到 0001”方向追加文件。失败包已改名为
  `.cdmod.123` 停用，加载器中仅为该实验增加的合成 source 复制逻辑也已撤回。
- 新探针改为直接修改 Human Female standalone `N20260707125900.cdmod` 内的
  `meshparam_example_damian.xml`：头部 XML `Index=0` 的 skeleton、mesh、icon 三处资源 ID
  从 `0001` 等长改为 `0027`，不复制或重命名任何头部/DDS。探针包 SHA-256 为
  `6c3b0952c6714d281d3e289f3b4b05a80a18d5f5f71479d648943d55a944837d`；PAMT 完全不变，
  13,279,249 字节 PAZ 仅有 6 个加密字节变化，VFS `0038` 重读确认 Index=0 三处均为 0027，
  `nppsa` 不再包含旧 27→1 覆盖。该“界面槽位直接引用原生身份”方案仍需用户用主菜单
  重新加载存档验证妆容持久化，确认前不得扩展到五张女巫脸。
- 这种包仍受源资源存在性约束：游戏更新后若 0001/0027 路径或资源链变化，严格
  构建应明确失败或报警，不能静默输出残缺头部。后续同类头部/身体替换应先枚举完整
  依赖链，再决定 copy-entry 集合，不得按某个具体模组名硬编码加载器逻辑。

- `Equip Everything V6.json` 是 DMM 发布的 `Format 3` 形态，独立版已可通过语义转换生效。单独只装该模组时，用户已确认能进游戏且模组生效。
- `Female Rapier and Shield Module.json` 与 `Equip Everything V6.json` 曾造成装备表/物品表组合冲突。处理这类冲突时，应优先比较最终语义列表；如果后续模组的新列表是当前扩展列表的子集，不应把已扩展列表缩短。
- `LMBits_Spirit_Infinite.json` 与 `LMBits_Stamina_Infinite.json` 单独安装可生效，多模组时曾触发无响应。排障后确认这类 JSON 需要对齐 DMM 的 patch 顺序和重复 offset 行为，不能简单做“同 offset 后者覆盖前者”。
- `.pabgb` 输出应尽量带 companion `.pabgh`，尤其是 JSON/Format 3 合成后可能影响表索引时。即便没有插入导致的长度变化，也要谨慎保持表头/索引一致。
- `LARGER STACKS AND ENHANCED INVENTORY v7.json` 是 `format: 2`，目标包括 `gamedata/inventory.pabgb` 和 `gamedata/iteminfo.pabgb`，其中 `iteminfo` 约 4974 条 change。单独诊断所有 change 可应用，因此它本身不像坏表；更可能是大 JSON、大 overlay、VFS hook 面和启动 I/O 时序共同放大了偶发无响应。
- VFS runtime 日志默认应保持安静，不要在正常启动时记录每条 redirect、handle map、IAT hook installed。需要细查时再通过 `VFS_DEMO_VERBOSE_LOG=1` 打开详细日志。
- VFS runtime 默认不应 patch `.asi` 模块的 IAT。用户实测禁用/绕开 ASI hook 后，游戏运行中无响应明显减少；`SuperJump.log` 曾出现 `another mod hooked first`、`hook site already patched; chaining`，说明 ASI 本身也会做 native hook，多 ASI 与 VFS hook 共存有风险。

### 已证伪或需要避免的方案

- 不能粗暴只 patch 游戏目录内模块。曾经这样改后，游戏能启动但模组全部不生效，说明 Crimson Desert 的关键 PAZ/PAMT 读取链路可能经过外部 DLL、运行库或系统模块。
- 不能把外部 DLL、系统 DLL、运行库模块全部排除出 VFS hook。后续只能细化到“特定高风险 ASI / 特定 API / 特定模块”，不能一刀切缩小 hook 面。
- 不能为了减少无响应而跳过全部 Format 3 或 JSON 大 patch。`Equip Everything V6.json`、`LMBits_*`、`LARGER STACKS...` 单独或部分组合都已经确认能生效，问题更像组合加载和运行时稳定性，而不是单个模组必坏。
- 不能只看 `run_cdmm_vfs.bat` 的 smoke test 通过就认为稳定。smoke test 只能证明进程保持运行，仍要观察游戏加载阶段和实际游玩阶段是否无响应。

### 后续继续优化时优先记录的日志

每次复现“启动黑屏无响应 / 加载中无响应 / 游玩中无响应”时，优先保留以下信息：

```text
T:\C++\vfsDmoe\logs\vfs_launcher.log
T:\C++\vfsDmoe\logs\vfs_runtime.log
C:\Users\liuho\AppData\Local\Pearl Abyss\log\Launcher_*.log
C:\Users\liuho\AppData\Local\Pearl Abyss\DumpCache
C:\Users\liuho\AppData\Local\CrashDumps
C:\ProgramData\Microsoft\Windows\WER\ReportArchive
G:\SteamLibrary\steamapps\common\Crimson Desert\.cdloader\vfs_mapping_tree.json
G:\SteamLibrary\steamapps\common\Crimson Desert\.cdloader\vfs_active\NNNN\0.paz
G:\SteamLibrary\steamapps\common\Crimson Desert\.cdloader\vfs_active\NNNN\0.pamt
G:\SteamLibrary\steamapps\common\Crimson Desert\.cdloader\logs\cold_load.log
G:\SteamLibrary\steamapps\common\Crimson Desert\.cdloader\logs\hot_load.log
```

同时记录：

- 本次启用的 JSON/Format 3/loose/standalone/DDS 模组列表。
- 本次 overlay 编号、映射文件数、`0.paz` 大小。
- 最新 `Launcher_*.log` 中 `Start Load SaveSlot*` / `End Load SaveSlot*` 后面的第一条 `ERROR`。游戏日志虽然叫 Launcher，但包含运行期数据表、存档加载、资源读取错误，是定位存档无响应的第一优先证据。
- Windows 事件查看器 `Application` 日志里的 `Application Hang` / `Windows Error Reporting` / `Application Error`，尤其要区分 `CrimsonDesert.exe` 主进程无响应和 `crashpad_handler.exe` 退出链路崩溃。
- 是否存在 `bin64/*.asi`，尤其是 `SuperJump.asi`、`FreedomFlyer.asi`、`SuperAxiomForce.asi`、`ImprovedAxiomManeuver.asi`、`OpenStorageAnywhere.asi`、`CharacterCreatorHead.asi`。
- 是否启用了 `VFS_DEMO_ENABLE_NT_OPEN_FILE=1`、`VFS_DEMO_VERBOSE_LOG=1`、`VFS_DEMO_PATCH_ASI_MODULES=1`。
- `run_cdmm_vfs.ps1` 针对 Crimson Desert 默认按“ASI 是游戏原生文件”处理：会清理进程级 `VFS_DEMO_PATCH_ASI_MODULES` 残留，并且默认不传 `-EnableNtOpenFileHook`。只有需要诊断 NT 层缺口时才显式追加 `-EnableNtOpenFileHook`；只有需要复现旧行为时才显式追加 `-PatchAsiModules`。
- 2026-07-10 新经验：如果 VFS/模组构建后游戏稳定在 LOGO 阶段秒退，且 Windows 事件只显示 `CrimsonDesert.exe 0xc0000005` / `crashpad_handler.exe`，不要立刻认定某个新 loose/standalone 包损坏。用户实测需要先通过 Steam 原生方式启动并进入一次游戏，让 Steam/游戏完成启动态初始化；之后再走 cdmm/VFS 启动即可恢复。后续排 LOGO 秒退时，应把“是否刚更新/验证/清理后还没先用 Steam 原生进过一次”作为前置检查项。
- 2026-07-10 新经验：同一套 VFS/模组可能出现“上一轮 LOGO 未响应，下一轮成功进入”的交替现象。用户确认 `CharacterCreatorHead.asi` 与 `N20260707170705` 都不是单点坏模组：前者在未响应/强退链路中容易被 crashpad 或 Windows 事件带出，只能当伴随噪声；后者在成功场次仍出现在 `.cdloader/load_order.json` 第 38 项。排障时应优先比较成功/失败两场的 `.cdloader/load_order.json`、`.cdloader/vfs_state.json` 指纹、`vfs_mapping_tree.json` 映射数量、PAPGT 包顺序、standalone 分配编号和 VFS runtime 映射文件数，再判断是否是加载顺序、分包缓存或启动时序问题，不要只因为某个稳定 ASI 报错就改 ASI 处理逻辑。
- 2026-07-10 新经验：当用户明确说“DMM 能正常进游戏，但 cdloader/VFS 某一轮进不去”时，不能因为游戏实体包或 `meta` 被 DMM 改动过就直接判定“DMM 污染是根因”。DMM 此时反而是成功 oracle，应比较 DMM 成功产物与 cdloader 失败产物。下次复现 cdloader 失败时，先不要 Steam 验证、不要重新构建、不要覆盖 `.cdloader`，第一时间备份失败现场的 `.cdloader/vfs_active`、`.cdloader/vfs_state.json`、`.cdloader/vfs_mapping_tree.json`、`T:\C++\vfsDmoe\logs\vfs_runtime.log`、最新 `Launcher_*.log` 和 Windows Application 事件。重点比较失败场和成功场的 `last_fingerprint`、`nppsa/0.paz` 大小、`nppsa/0.pamt` hash、映射文件数、PAPGT 包顺序，以及是否有 `.cdloader/vfs_package_cache` 复用旧包。2026-07-10 一次现场中，DMM 备份和 Steam 验证后成功场的 `nppsa` 大小/hash 不同，且 21:52 日志里的 gliding `.paa` 缓冲区异常对应的当轮失败包已经被后续构建覆盖；因此后续必须先冻结失败包再解析 PAMT，不能事后只看新包下结论。
- 已记录的一次成功对照：`Launcher_2026_07_10_13_09_48_36360.log` 正常初始化，`vfs_runtime.log` 为 `mapping manifest loaded files=28 directories=14`，`.cdloader/vfs_state.json` 的 `last_fingerprint=d06f1781ccbba8c6412537b5c87f7aa61a4047013822cc1ce5dcb1b2a5f5997f`，`load_order_count=55` 且包含 `N20260707170705`。后续若失败场次映射数、指纹或排序不同，先按加载产物差异排查。
- 第一次启动成功、第二次启动无响应这类“热启动/快速恢复”现象要单独标记，后续需要比较冷启动和热启动的 mapping、PAMT、PATHC、模块 patch 顺序。
- 2026-07-10 VFS 发布包运行库经验：用户机器已经安装 `Microsoft Visual C++ v14 Redistributable (x64) 14.51.36247`，但旧版 `cdloader-VFS-v2.exe` 仍弹 `MSVCP140D.dll`、`VCRUNTIME140D.dll`、`VCRUNTIME140_1D.dll`、`ucrtbased.dll` 缺失。根因不是用户 VC 运行库版本低，而是 cdmm 的 `private/vfs_runtime/nppvfs_launcher.exe` 和 `vfs_runtime.dll` 曾误用 `T:\C++\vfsDmoe\bin\x64\Debug` 产物，Debug CRT 不随 Microsoft VC Redistributable 发布。给用户发布或重新打包 `cdloader-VFS-v*.exe` 前，必须先从 `T:\C++\vfsDmoe\bin\x64\Release` 复制 Release x64 产物：`vfs_launcher.exe` 改名为 `private/vfs_runtime/nppvfs_launcher.exe`，`vfs_runtime.dll` 覆盖 `private/vfs_runtime/vfs_runtime.dll`，再运行 `build_cdloader_vfs_nuitka.bat`。发布前必须用二进制字符串或依赖工具确认内置 runtime 不包含 `MSVCP140D.dll`、`VCRUNTIME140D.dll`、`VCRUNTIME140_1D.dll`、`ucrtbased.dll`，只允许普通 `MSVCP140.dll`、`VCRUNTIME140.dll`、`VCRUNTIME140_1.dll` 等 Release 运行库依赖。用户实测替换 Release runtime 并重新打包后的 `T:\python_pro\cdmm\dist_nuitka\cdloader-VFS-v2.exe` 可成功运行。

### 2026-07-09 游戏日志定位实体包污染经验

本次存档无响应最终不是 cdloader VFS 生成包直接损坏，而是游戏原始 `0009/0.paz + 0009/0.pamt` 被旧实体写入/DMM/模组流程污染。关键证据来自游戏自己的日志，而不是 cdloader 日志：

```text
C:\Users\liuho\AppData\Local\Pearl Abyss\log\Launcher_2026_07_09_*.log
```

失败日志在 `End Load SaveSlot105` 之后反复出现：

```text
파일 읽기 버퍼 사이즈 비정상:
character/motion/1_pc/2_phw/weapon/3_shield/cd_phw_basic_00_00_air_base_gliding_*.paa
```

日志中的两个数字对应 PAMT entry 的 `comp_size/orig_size`。排查时解析当前注册的 PAMT，发现这些异常 gliding `.paa` 并不在 `.cdloader/vfs_active/nppsa/0.pamt`，而是在游戏根目录 `0009/0.pamt` 中；同时 `0009/0.paz` 的修改时间和大小异常，说明卸载 `mods/Male Glide Animation` 不能恢复已经写入原始包的内容。

修复方法是用 Steam 验证游戏完整性或 DMM restore/unmount 恢复原始包。验证后 `0009/0.paz` 从异常大小恢复到原版大小，`0009/0.pamt` 中同名 gliding `.paa` entry 回到原版小尺寸，重新冷构建 VFS 后用户确认可以稳定进入存档并运行。后续遇到资源读取缓冲区大小异常时，必须先检查目标 entry 当前到底来自 `.cdloader/vfs_active`、standalone overlay，还是游戏根目录原始 `NNNN/0.pamt`，不要只看当前 `mods` 目录是否已经卸载。

### 2026-07-09 loose PAA 重打包压缩尺寸经验

`Male Glide Animation` 后续确认并非只有“旧实体包污染”问题。Steam 验证恢复 `0009` 后，把该模组重新通过 VFS loose overlay 加载，游戏仍会在进入存档附近报：

```text
파일 읽기 버퍼 사이즈 비정상:
cd_phw_basic_00_00_air_base_gliding_move_dash_f_00_at_shield_01.paa, 39671, 39646
cd_phw_basic_00_00_air_base_gliding_move_acceleate2_f_ing_00_at_shield_01.paa, 40285, 40222
cd_phw_basic_00_00_air_base_gliding_std_idle_00_at_shield_01.paa, 37322, 37239
```

这次异常 entry 来自 `.cdloader/vfs_active/nppsa/0.pamt`，不是原始 `0009`。关键原因是加载器沿用了 vanilla entry 的 `compression_type=2`，但该模组提供的 `.paa` 是 PHM 动画数据放在 PHW 文件名下，部分文件重新 LZ4 后反而比原始内容更大，导致生成的 PAMT 出现 `comp_size > orig_size`。Crimson Desert 对普通 LZ4 entry 不接受这种尺寸关系，会记录韩文“文件读取缓冲区大小异常”并无响应。

通用修复已经加在 `services/overlay_service.py`：普通 `compression_type=2` entry 打包时先尝试 LZ4；只有 `len(compressed) < len(raw)` 时才写压缩 payload 和 flags=2。若压缩后不省空间，必须降级写原始 payload，flags=0，`comp_size == orig_size == len(raw)`。不要为了“保持 vanilla flags”而生成 `comp_size > orig_size` 的记录。

本次真实模组验证结果：

```text
Male Glide Animation: checked 59, missing 0, bad_comp_gt_orig 0
raw_fallback_from_type2 4
```

其中 4 个原本会按 type2 压缩但压缩后变大的 `.paa` 已自动降级为原始写入，覆盖了日志中报错的 3 个 gliding 文件。后续遇到 `.paa`、`.paac`、`.wem` 或其他 loose 二进制替换时，排障必须同时比较：

- loose 源文件 `len(raw)`。
- `lz4_compress(raw)` 后的长度。
- vanilla entry 的 `compression_type`、`comp_size`、`orig_size`。
- 最终 overlay/VFS PAMT 中的 `comp_size/orig_size/flags`。

如果游戏日志中的两个数字前者大于后者，优先怀疑 overlay 重打包尺寸/flags 不自洽，而不是先怀疑资源路径或 VFS hook。

### 2026-07-09 Fast Pickup / interactioninfo Format 3 经验

`Fast_Pickup_Instant_Pickup.json` 是 `format: 3`，目标不是传统 byte offset，而是：

```text
target: interactioninfo.pabgb
field: interaction_type
entry/key:
  Gimmick_PickUp / 1000004
  Gimmick_Collect / 10028
new: 11
```

游戏更新后“不生效”的直接原因不是该 JSON 的 key/name 消失，而是独立加载器之前没有 `interactioninfo` table writer，Format 3 会被识别但无法生成 byte patch。当前已新增 `services/format3_interactioninfo_writer.py`，只放开 `interaction_type / _interactionType` 这一窄字段。

真实表验证结果：当前游戏 `gamedata/interactioninfo.pabgb` 中两个目标记录仍存在，原始字节均为 `00`，writer 会生成：

```text
Gimmick_PickUp.interaction_type: 00 -> 0b
Gimmick_Collect.interaction_type: 00 -> 0b
```

定位经验：`interaction_type` 对应该表记录 payload 的第 1 个字节；原版 `Gimmick_PickUp_NoAction` 的同位置已经是 `11`，与“快速拾取/无拾取动作”的语义吻合。后续遇到 `interactioninfo.pabgb` 的 Format 3 模组时，不要用通用 schema parser 盲写；该表中间有复杂字段，通用 parser 容易被字符串/数组带偏。必须按 key 优先、entry 名称校验、唯一名称回退的方式安全定位。

`Fast_Pickup_Increase_Range.json` 是同一组快速拾取的“增加交互范围”部分，仍然是 `format: 3` / `interactioninfo.pabgb`，但字段为：

```text
interaction_pivot_list[0].raw_a
interaction_pivot_list[0].raw_b
```

其中 `1084227584 == 0x40a00000 == 5.0f`，`1077936128 == 0x40400000 == 3.0f`。当前已在 `services/format3_interactioninfo_writer.py` 增加最窄支持：解析 `key + string_key` 后的前缀、`LocalizableString`、`pivot_selection_target`、`CArray` 计数，并只定位第一个 `InteractionPivotData` 的 `raw_a/raw_b`。真实诊断结果：

```text
Fast_Pickup_Increase_Range: intents 10, changes 9, skipped 1
Gimmick_PickUp raw_a/raw_b: 2.5 -> 5.0
SmallAnimal_Skin raw_a: 1.5 -> 3.0
SmallAnimal_Skin raw_b: 2.0 -> 3.0
Animal_Skin raw_a: 1.7 -> 3.0
Animal_Skin raw_b: 2.0 -> 3.0
Gimmick_Collect raw_a: 2.5 -> 3.0
Gimmick_Collect raw_b: 原版已是 3.0，跳过
Insect_Catch raw_a: 2.5 -> 3.0
Insect_Catch raw_b: 2.6 -> 3.0
```

后续若继续扩展 `interactioninfo`，优先参考 `T:\C++\mod-workbench\dmm-parser-rust-only\src\tables\interaction_info\info.rs` 的字段顺序，但 cdloader 仍应只迁移真实模组需要的窄字段，避免做未验证的全表 serializer。

### 2026-07-09 宠物拾取 / 批量拾取研究经验

用户提出想做“互相挨着的东西一次性拾取，范围约 0.5m”。本轮确认游戏确实有宠物拾取相关数据，但它不是普通 Fast Pickup 的简单距离字段。

已确认 `gamedata/interactioninfo.pabgb` 中存在：

```text
Pet_LootingItem / key 1000052
raw_a = 0x40a00000 = 5.0f
raw_b = 0x40000000 = 2.0f
```

`gamedata/conditioninfo.pabgb` 中存在：

```text
InteractionTarget_PetLooting / key 1000069
Macro(InteractionTarget_PetLooting) / key 4294967249
```

该条件表达式包含 `hasLootItem`、`islootable`、`Dead_Loot_Pet`、`IsPetLooting`、`Pet_LootingItem`、`ThrowItem`、`LootDrop` 等关键词。`mod-workbench` 中对应的条件枚举包括 `ConditionData_HasLootItem=25`、`ConditionData_IsLootable=28`、`ConditionData_IsPetLooting=154`。

重要边界：`Pet_LootingItem` 字符串只可靠出现在 `interactioninfo.pabgb` 和 `conditioninfo.pabgb`，`InteractionTarget_PetLooting` / `IsPetLooting` 也只在 `conditioninfo.pabgb` 中以字符串形式出现。普通玩家拾取交互没有明显直接引用宠物拾取宏。因此当前不能承诺纯 Field JSON / 数据补丁可以实现“玩家按一次拾取键自动枚举周围多个地面物”。`interactioninfo` 的 `raw_a/raw_b` 能改交互距离，但不等于能把单目标交互变成多目标循环。

后续可安全推进的方向：先增强普通 Fast Pickup，或研究 `Pet_LootingItem`、`BuffLevel_AdditionalPetFriendly`、`Equip_Socket_AddPetFriendly`、`Equip_Passive_Pet_*` 是否能增强宠物拾取体验。不要盲目把普通 `Gimmick_PickUp` 条件替换成 `InteractionTarget_PetLooting`，否则可能绕过偷窃、任务物、非法拾取等目标合法性判断。详细记录见 `docs/pet-pickup-batch-loot-research.md`。

### 当前仍未解决的问题

- 多 JSON、大 `iteminfo.pabgb` patch、大 DDS overlay 混合时，启动阶段仍偶发无响应，重试多次可能成功。
- ASI 与 VFS hook 的边界还需要继续细化，目前只确认“默认跳过 `.asi` 模块 IAT patch”有帮助。
- 需要评估 overlay 分包策略。单个 overlay `0.paz` 过大时，可能需要按来源或类型分包，但必须保持同一 entry 的合成结果唯一，不能让分包重新引入互相覆盖。
- 需要继续比较 DMM 实体写入结果与 cdloader/VFS 虚拟结果，尤其是 `iteminfo.pabgb`、`skill.pabgb`、`inventory.pabgb`、`equipslotinfo.pabgb` 这类高风险表。
- 2026-07-09 新排查结论：禁用全部 `loose_files` 后稳定；改为只禁用 `dds` 后，纯 loose 启用、DDS/PATHC/贴图资源组禁用，用户确认可以正常进入存档。因此“加载存档无响应”的第一嫌疑已经收窄到带 DDS 的 loose 资源组。该配置下仍无法在游戏内从一个存档切换到另一个存档，这个问题先作为纯 loose/切档独立问题记录，当前主线优先解决加载存档无响应。
- 2026-07-09 DMM 成功现场对照后，VFS 继续贴近 DMM 分包：新增 `nppvoice`，把 `.wem` 语音 loose 从 `nppsa` 拆出；PAPGT 前置顺序调整为 `nppv3_statusinfo -> nppv3_stringinfo -> nppv3_equipslotinfo -> nppv3_iteminfo -> nppvoice -> nppgen -> nppsa`。当时 `nppv3_statusinfo` 还只是预留顺序/包位，statusinfo writer 尚未实现；该历史限制已被2026-07-12的writer实现与实机验证、以及2026-07-16的无齿轮直接生效验证覆盖。`vfs_state` schema 已提升到 2，避免复用旧单包缓存。

### 已验证成功：DMM-like VFS 分包与 PAPGT flags

2026-07-08 后续实机验证确认：VFS 模式改成 DMM-like 多独立包后，再把 `meta/0.papgt` 中所有注册目录 flags 统一规范化为 `003fff00`，游戏可以正常进入，且用户确认模组全部生效。

已验证的 VFS 包顺序：

```text
nppv3_equipslotinfo
nppv3_stringinfo
nppv3_iteminfo
nppgen
nppsa
standalone assigned dirs...
vanilla dirs...
```

关键点：

- 仅拆成多个包还不够，必须同时把 PAPGT flags 对齐 DMM 成功现场。
- DMM 成功现场中 `nppv3_*`、`nppgen`、`nppsa` 和 vanilla 目录 flags 全部是 `003fff00`。
- 旧 VFS 曾保留原版混杂 flags，例如 `007fff00`、`00000101` 等，分包后仍会启动失败。
- VFS 构建应继续只写 `.cdloader/vfs_active` 与 `vfs_mapping_tree.json`，不要直接污染游戏源文件。
- 普通实体 apply 暂不强制套用该 flags 规范化；当前已验证的是 VFS 模式。

### 2026-07-09 VFS 冷构建性能优化经验

本轮明确：完整 GUI 管理器已经不能作为性能/Format 3 上游，cdmm 当前链路已经走在前面。后续优化以 cdmm 自己的 profile、DMM 成功现场和实机结果为准。

强制绕过整套 VFS 指纹缓存的冷构建 profile 显示，最初当前 50+ 模组集合约 115s；主要瓶颈是：

- 纯 Python `hashlittle` 对大 PAZ/PAMT buffer 计算完整性 hash。
- Format 3 动态 entry-relative 变长补丁每条 intent 重建整表 entry bounds。
- `fixup_pabgh_after_inserts()` 对每个 PABGH pointer 重复扫描 insert 列表。
- loose 阶段重复生成 PAMT signature、重复解析原始 PAMT 路径映射。
- PAPGT 阶段对未修改 vanilla 目录重新读盘计算 PAMT hash。

已落地优化：

- `hashlittle` fallback 改为小端 `memoryview.cast("I")` 批量读 u32，保持旧算法等价。
- 已新增 cdmm 自己的闭源/独立 C++ native 模块 `_cdloader_native`，源码放在 `T:\C++\cdloader_native`，项目内只提交构建产物 `native/_cdloader_native.cp310-win_amd64.pyd` 与 `native/_cdloader_native.cp312-win_amd64.pyd`。Python 侧通过 `cdloader_native.py` shim 调用，`common/hashlittle.py` 优先走 native，fallback 到 Python。打包脚本必须把 `cdmm.native._cdloader_native` 收进 exe。
- `build_papgt()` 对未修改目录复用原 PAPGT 内保存的 PAMT hash，不再读 live `0.pamt` 重新计算。
- Format 3 `_apply_dynamic_body_changes()` 改为初始构建一次 name offset，后续按长度变化增量平移，避免每条动态 change 全表重扫。
- `fixup_pabgh_after_inserts()` 改为 insert 前缀和 + 二分查找，避免 O(pointer_count * insert_count)。
- `get_game_pamt_index()` 增加单进程按游戏目录复用，避免 loose 阶段数千次重复生成 PAMT signature。
- VFS 分包增加 `.cdloader/vfs_package_cache`。当全局模组指纹变化但某个 DMM-like 分包输入未变时，直接复用缓存的 `0.paz/0.pamt` 和 built-entry 元数据，不再重打包、不再重新跑大包 `hashlittle`。
- 传统 JSON `pattern_scan` 已接入 native/fallback 范围扫描，`services/json_loader.py` 不再把 `bytearray` 转成完整 `bytes` 再扫描，避免大 effect/json 目标重复复制。
- `pamt_index_service.register_target()` 对同 basename 的不同路径写法（例如 `effect/binary__/.../x.paem` 解析到 `effect/x.paem`）不再重复补扫已加载 PAMT，避免 12 个 effect 目标反复扫 33 个原版 PAMT。
- numbered loose 不再混入全局 PAMT basename 预筛选；`files/NNNN/...` 走目录级 exact/basename 批量预取，root loose / JSON / Format 3 才进入全局 target-driven 预筛选。
- `archive/pamt.py` 增加 filtered PAMT parser：冷扫 PAMT 时只构造目标 basename / exact 命中的 `PazEntry`，不再先生成全游戏 160 多万条 entry 再过滤。
- filtered PAMT parser 的热路径不要对每条 entry 调 Windows `os.path.basename()` / `ntpath.basename()`。PAMT `node_path` 已经是当前 folder 下的路径主体，直接用 lower 后的 `node_path` 判断 basename，并只在命中后构造完整 `full_path` / `PazEntry`。

验证结果：

```text
强制冷构建：约 115s -> 约 58-61s
绕过整套 VFS 指纹缓存但命中分包缓存：第 1 次约 60.8s，第 2 次约 6.5s
2026-07-09 C++ native + JSON/PAMT 优化后，用户真实 VFS 构建：61.81s -> 16.10s
继续优化 filtered PAMT basename 热路径后，删除 pamt_target_cache/vfs_state、保留分包缓存的开发冷构建：VFS 总耗时 9.80s；loose 目标匹配 6.16s -> 3.57s；JSON 约 28.29s -> 0.27s；Format 3 约 11.87s -> 2.55s
pytest test -q: 164 passed
ruff touched files: All checks passed
```

后续最大收益方向：

- 继续压 loose 目标匹配时，优先考虑 native PAMT filtered parser 或更细的目标级缓存。不要恢复 full-game `pamt_index_cache.json`，之前该缓存会膨胀到数百 MB 并拖慢热加载。
- 后续设计自定义高速模组格式时，优先考虑“预编译最终 overlay entry / 分包级产物 / table-level delta cache”，目标是减少冷构建时的解析、序列化、重打包和大 buffer hash，而不是只换 JSON 字段名。

## Format 3 当前状态

用户已确认：当前其他类型模组正常，部分真实安装已在游戏内生效。Format 3 仍不是全覆盖支持，但已经完成并验证了 `iteminfo.pabgb` 中 `prefab_data_list[N].tribe_gender_list` 的窄支持。

完整 GUI 管理器参考仓库：

```text
T:\python_pro\CrimsonDesert-UltimateModsManager
```

重点参考文件：

```text
src/cdumm/engine/format3_handler.py
src/cdumm/engine/format3_apply.py
src/cdumm/engine/iteminfo_writer.py
src/cdumm/engine/skill_writer.py
src/cdumm/_vendor/buffinfo_parser.py
schemas/pabgb_type_overrides.json
tests/test_format3_update_preserves_mod_id.py
tests/test_format3_target_basename_resolves.py
tests/test_iteminfo_multi_mod_compose.py
tests/test_skill_list_writer.py
```

已经确认并已修复的真实 Format 3 模组：

```text
G:\SteamLibrary\steamapps\common\Crimson Desert\mods\kliff_Wears_Damiane_Armor_Update_1.05.01.json
```

特征：

- `format: 3`
- `target: iteminfo.pabgb`
- 约 209 个 intents
- 字段类似：

```text
prefab_data_list[N].tribe_gender_list
```

完整版当前验证结果曾显示：

```text
supported=0, skipped=209
```

原因：

```text
nested-field writes are not implemented
```

独立版修复后，真实转换诊断结果：

```text
changes 209 skipped 0
```

用户随后真实 apply 并进游戏确认：该 Format 3 模组已经成功生效。

核心实现文件：

```text
services/format3_parser.py
services/format3_capabilities.py
services/format3_runtime.py
services/format3_loader.py
services/format3_iteminfo_writer.py
services/json_loader.py
services/loader.py
test/test_format3_iteminfo_writer.py
```

修复后的 apply 日志应该类似：

```text
INFO: 按 Format 3 basename 匹配 iteminfo.pabgb -> gamedata/iteminfo.pabgb
INFO: Format 3 bridge: 处理 1 个模组、1 个目标，209 个生成补丁
```

如果重新出现：

```text
INFO: Format 3 bridge: 处理 1 个模组、1 个目标，0 个生成补丁
INFO: Format 3 bridge: 跳过 209 个 intent
```

说明 `iteminfo` writer 或目标 base 合成又退化了。

## Format 3 当前代码结构

2026-07 这一轮重构后，独立版 `Format 3` 的职责已拆分如下：

```text
services/format3_parser.py
  负责读取 format:3 JSON，统一处理 target/intents 与 targets[]，
  支持 key 缺省为 0、op 缺省为 set。

services/format3_capabilities.py
  负责声明“某个 table 当前明确支持哪些字段形态”。
  当前 iteminfo 只显式声明支持：
  prefab_data_list[N].tribe_gender_list

services/format3_runtime.py
  负责 writer 运行时上下文、DispatchResult、SkippedIntent、跳过原因摘要。

services/format3_loader.py
  负责目标解析、base 合成、能力筛选、writer 分发、warning 汇总。

services/format3_iteminfo_writer.py
  当前仍是窄 writer，但已经支持真实模组验证过的 iteminfo 子集：
  prefab_data_list[N].tribe_gender_list
  drop_default_data.drop_enchant_level
  drop_default_data.socket_item_list
  drop_default_data.add_socket_material_item_list
  drop_default_data.default_sub_item
  drop_default_data.socket_valid_count
  drop_default_data.use_socket
  以及已迁入的 whole-table primitive 字段（如 cooltime、equipable_hash 等）。

2026-07-09 新增 `services/format3_iteminfo_record_writer.py`：用于 DMM `ItemBuffs`
类武器特效 Field JSON 的单记录写入，避免把少量字段改动送进慢且不完整的
whole-table parser。已用 DMM 成功现场验证：

```text
G:\SteamLibrary\steamapps\common\Crimson Desert\mods\AeserionSpear_Flame.field.json
G:\SteamLibrary\steamapps\common\Crimson Desert\mods\CirclingMoon_Flame.field.json
G:\SteamLibrary\steamapps\common\Crimson Desert\mods\DivergingMoon_Flame.field.json
G:\SteamLibrary\steamapps\common\Crimson Desert\mods\GraspingMoon_Flame.field.json
G:\SteamLibrary\steamapps\common\Crimson Desert\mods\RighteousVerdict_Lightning.field.json
```

DMM oracle：

```text
G:\SteamLibrary\steamapps\common\Crimson Desert\dmmv3_iteminfo\0.paz
G:\SteamLibrary\steamapps\common\Crimson Desert\dmmv3_iteminfo\0.pamt
```

关键结论：

- `nppv3_iteminfo` 与 `dmmv3_iteminfo` 的 `iteminfo.pabgb` / `iteminfo.pabgh`
  已 byte-match。
- 支持字段包括 `cooltime.a/b/c`、`docking_child_data`、
  `docking_child_data.gimmick_info_key`、`equip_passive_skill_list`、
  `gimmick_info`、`item_charge_type`、`max_charged_useable_count.a/b/c`、
  `respawn_time_seconds`。
- 完整 `docking_child_data` 使用 DMM 扩展结构，包含 `unk_docking_108`、
  `inherit_summoner`、`summon_tag_name_hash`。
- 完整 docking 插入不是只替换 1 字节 optional flag，而是替换 4 字节空占位
  `00 00 00 00`；漏掉这点会让 record 比 DMM 多 3 字节并推歪 `.pabgh`。
- `respawn_time_seconds` 按 DMM 成功包写 4 字节 u32，不要按旧 parser 的 i64
  覆盖 8 字节，否则会破坏相邻尾部字段。
- 同一 record 内多次写 `max_charged_useable_count.a/b/c` 时，定位器不能要求
  三个旧值始终相等；写完 `.a` 后会临时变成 `[new, old, old]`。
  记录定位必须 key 优先、entry 名称回退、同名歧义安全跳过。
```

后续任何人继续做 `Format 3`，都必须遵守：

1. 新增 table 支持时，先补 `format3_capabilities.py`，明确“当前支持哪些字段”。
2. writer 返回必须走 `Format3DispatchResult`，不要只返回裸 `(changes, skipped_count)`。
3. 跳过原因要可聚合、可读，不能重新退化成只有“没有可应用 intent”。
4. 没有足够把握时，宁可安全 skip，也不要模糊命中后误写二进制。

## Format 3 / DMM 对照开发经验

2026-07-09 适配 `Equip Everything V6.json`、`OneHandSwordFiveSockets.json`、`OneHandShieldFiveSockets.json` 等 Format 3 iteminfo 模组时，最终靠 DMM 成功现场反推 writer，而不是只按字段名猜结构。这个流程后续必须继续沿用，尤其是新增 `skill`、`statusinfo`、`buffinfo`、`conditioninfo` 等高风险 table 时。

推荐流程：

1. 让用户用 DMM 成功加载同一批模组，并确认游戏可进入、功能生效。
2. 保留 DMM 现场文件：`mount_log.txt`、`dmmv3_<table>/0.paz`、`dmmv3_<table>/0.pamt`、游戏 `Launcher_*.log`。
3. 用 CDMM/VFS `BuildOnly` 生成 `.cdloader/vfs_active/nppv3_<table>`。
4. 解压 DMM 与 CDMM 对应 table，比较 `.pabgb` body 长度、`.pabgh` offset、每条 record 长度、record bytes。
5. 从差异反推 writer 的真实字段位置和序列化方式；不要因为 JSON 字段名看起来明确就直接写全表 serializer。
6. 修复后必须让 CDMM 输出与 DMM 成功输出在目标 table 上逐字节对齐，再加 synthetic 回归测试锁住。

本次 iteminfo 的关键结论：

- `Format 3` 不能只转成传统 JSON byte patch 后二次套用。`build_format3_overlay_entries()` 应优先返回内存合成后的最终 `pabgb/pabgh` overlay entry。
- `iteminfo.drop_default_data.*` 这类会改变单条记录长度的 intent，必须走单记录 roundtrip 合并写入；多 entry 连续变化时要在每次长度变化后刷新 `.pabgh`，避免后续 offset stale。
- `_apply_dynamic_body_changes()` 只能在当前 entry 里应用 entry-relative change。不要跨 entry 做远距离 pattern relocation，否则补丁可能跑到错误记录。
- `socket_valid_count` / `use_socket` 要对齐 DMM 行为：当 `default_sub_item.type_id != 14` 时，写 `default_sub_item.value` 的低字节，而不是写 `DropDefaultData` 末尾两个 u8。
- `prefab_data_list` legacy 块定位要兼容无 `FF00` 尾标、后续 prefab element scale 不是固定 `1.0` 的形态；只能在结构消费、prefab hash、唯一候选都满足时写入。
- 本次验证中，CDMM 生成的 `nppv3_iteminfo` 与 DMM 成功现场 `dmmv3_iteminfo` 解包后已对齐：body 长度差为 0、坏记录为 0、record 长度差为 0、byte diff 为 0。

DMM 日志中优先关注这些信号：

```text
[V3_COLLECT]
[V3_PREAPPLY]
[V3_OVERLAY]
[PRE_STANDALONE] Using standalone body as v2+v3 base
[V3_MATCH]
```

遇到“DMM 能进游戏，CDMM 闪退/无效果”时，优先比较最终产物，不要先扩大 hook 面或盲目回滚 writer。真正的目标是让同一个 table 的最终 bytes 追平 DMM 成功输出。

## Format 3 成功实现原理

这次不是做“通用 Format 3 全支持”，而是先解决真实模组需要的窄路径：

```text
target: iteminfo.pabgb
field: prefab_data_list[N].tribe_gender_list
```

实现思路：

- `format3_loader.py` 解析 `format: 3` 的单目标 `target/intents` 和多目标 `targets[]`。
- `format3_parser.py` 当前还支持 `key` 缺省为 `0`、`op` 缺省为 `set` 的新导出语法。
- Format 3 target 查找优先低编号 vanilla、`gamedata/`、完整路径，避免旧 overlay 或同 basename 的 UI 文件污染。
- `iteminfo.pabgb` 必须同时读取 companion `iteminfo.pabgh`，用 `.pabgh` 建立 `key -> PABGB entry offset`。
- 每个 PABGB entry 解析出 `entry name` 和 `name_end`，生成传统 byte patch 使用 `entry + rel_offset`，避免全局 offset 因前面 entry 长度变化而漂移。
- `format3_iteminfo_writer.py` 先尝试按 ItemInfo 字段顺序走到 `_prefabDataList`。
- 当前窄 writer 在记录命中策略上是：`key` 优先，`entry` 名称回退，名称命中多个记录时必须安全跳过。
- 当前游戏真实 `ItemInfo` 前置结构和完整管理器 schema/注释存在漂移，尤其 `ItemIconData` 附近容易错位；因此 writer 加了一个受限 fallback。
- fallback 只在当前 entry 内扫描形状像 `CArray<PrefabData>` 的候选，并且要求目标 `tribe_gender_list` 原始值非空且必须是 Format 3 新值列表的子集。
- 只有候选唯一时才生成 patch；候选为 0 或多个都跳过，避免误写。
- 新值按 `CArray<u32>` 写入：`u32 count + count * u32`。
- 生成的 patch 再交给传统 JSON byte patch 流程，复用已有 relocation、`entry + rel_offset`、PABGH fixup、overlay 写入。

这次能成功的关键判断：

```text
原始 tribe_gender_list 是新 tribe_gender_list 的子集
```

真实例子：

```text
original: 01000000d41d6cf9
patched : 02000000d41d6cf94bf6a1bf
```

含义是把原先 1 个 `u32` 扩成 2 个 `u32`，因此 `.pabgb` entry 长度会变化，后续必须依赖已有 `.pabgh` offset 修正逻辑。

## Format 3 已知可迁移方向

完整版作者的更新并不等于所有 Format 3 都支持。已经看到的方向包括：

- 支持 `targets: [...]` multi-target 解析。
- 支持部分 primitive field intent。
- 支持部分 `buffinfo` 专用路径。
- 支持部分 `dropset` / `skill` / `iteminfo` list writer。
- 修复更新时不重复导入 mod card、保留 mod id 等 GUI/数据库行为。

独立加载器迁移时不能直接照搬 GUI 管理器的数据库工作流，因为独立版没有完整 GUI DB 和导入状态模型。

## Format 3 还没做的重点

### iteminfo 嵌套字段写入

已完成窄支持：

```text
prefab_data_list[N].tribe_gender_list
```

但不是所有 iteminfo nested path 都完成。仍未完成类似：

```text
list_field[N].struct_field
list_field[N].sub_list[M].field
```

后续若要继续扩展，需要实现：

- 更完整的 PABGB schema 驱动 nested struct walker。
- 数组元素定位。
- 子字段序列化。
- 长度变化后的父结构重算。
- `.pabgh` offset 修复。

### 通用 dotted path writer

当前不应只为某个字符串硬编码。更稳的方向：

- 根据 schema / type override 解析 field path。
- 区分 primitive、struct、list、nested list。
- 每个 table writer 只处理自己能安全序列化的结构。
- 不能处理时明确 skip 并输出可读警告。

注意：当前 `prefab_data_list[N].tribe_gender_list` 是真实模组驱动的窄实现，允许保留；不要把它误删成“等通用 writer 再说”，因为用户已经游戏内确认生效。

另外注意：

- 尽管完整 GUI 管理器的 `iteminfo_writer.py` 已经支持更多 nested path / whole-table 能力，独立版当前**还没有**迁完。
- 不要因为参考仓库支持更多路径，就在独立版里把 `format3_capabilities.py` 直接放开；必须“实现一个能力，声明一个能力”，不要超前宣称支持。

### buffinfo 系统迁移

完整版有 `_vendor/buffinfo_parser.py` 可参考，但独立版尚未完整迁入。迁移前必须确认：

- 是否依赖完整管理器数据库。
- 是否依赖 GUI import 阶段生成的中间数据。
- 是否能在独立 apply 时直接从 vanilla + mod JSON 推导。

### Format 3 与 loose / JSON 的合成顺序

未来接入 Format 3 时，要继续保持合成思想：

```text
loose base -> 传统 JSON patch -> Format 3 semantic patch -> 最终 overlay entry
```

如果 Format 3 和 loose / JSON 同时改 `iteminfo.pabgb`，必须在内存中对同一份 base 连续应用，而不是最后一个 writer 覆盖前一个 writer。

## 真实成功样例

用户已经确认游戏内生效的组合包括：

```text
Better_Inventory_and_Trade_UI/files/0012
FemaleHumanIcon/0012
HumanFemale/0036/0.paz + 0.pamt
SwapButcherWithBarber/0036/0.paz + 0.pamt
UniEquip - 1.05.01 Update/files/gamedata
kliff_damiane_RTP_current_update.json
999WareHouse.json
More Inventory-JSON.json
FemaleArmorModule.json
kliff_Wears_Damiane_Armor_Update_1.05.01.json
```

最后排障确认：

- `UniEquip` 的 `files/gamedata` loose 生效。
- `kliff_damiane_RTP_current_update.json` 正确应用到 `gamedata/characterinfo.pabgb`。
- 没有再错误生成 `ui/characterinfo.pabgb`。
- JSON 类型模组仍然生效。
- 其他 loose / standalone 类型正常。
- `kliff_Wears_Damiane_Armor_Update_1.05.01.json` 的 Format 3 `iteminfo.prefab_data_list[N].tribe_gender_list` 已经游戏内确认生效。

## 后续 AI 修改建议

- 修改前先 `git status --short`，不要回滚用户已验证成功的改动。
- 不要因为看到大量 `__pycache__` modified 就执行破坏性清理。
- 优先补本地测试，尤其是同目标合成、basename 查找、`files/gamedata`、根部 `NNNN`、Format 3 skip 原因；但 `test/` 不要求纳入 git，按本地回归资料处理即可。
- 如果要继续做 Format 3，先在真实模组上打印 intent 统计和 skip 原因，再决定实现哪个 table writer。不要破坏当前已生效的 `prefab_data_list[N].tribe_gender_list` 窄支持。
- 不要承诺 Format 3 全支持；应明确说明“支持已实现 writer 的子集”。
- 每次真实 apply 前提醒用户最好保持游戏目录纯净，但加载器逻辑本身要能防旧 overlay 污染。
- 后续新增任何 `Format 3` 能力时，至少同步更新：
  `services/format3_parser.py`
  `services/format3_capabilities.py`
  `services/format3_runtime.py`
  `services/format3_loader.py`
  对应的 `test/test_format3_*.py`
- 当前独立版的 `Format 3 iteminfo` 已支持 `key` 优先、`entry` 名称回退、同名歧义跳过。后续如果改记录定位策略，必须补回归，避免把 name-only intent 支持做退化。

## Nexus Mods VFS 发布与隔离审核固定流程

用户只在 Nexus Mods 发布 `cdloader-VFS`。以后用户提出“发布新版”“打包 VFS”或类似要求时，AI 必须直接完成本节全部准备工作；用户只负责在 Nexus 上传文件、提交 VirusTotal 扫描和发送审核邮件。

### 版本与发布布局

- 版本号仍只允许修改项目根目录 `version.txt`，例如 `v4`；不得在 Python、C# 或 PowerShell 中重复硬编码发布版本。
- PE FileVersion/ProductVersion 必须由 `version.txt` 补齐为四段，例如 `v4 -> 4.0.0.0`。
- VFS 发布必须使用 `build_cdloader_vfs_nuitka.ps1` 的 Nuitka `standalone` 目录模式。
- 禁止恢复 `--onefile`，禁止 UPX，避免单文件自解包、运行时大批释放依赖和压缩壳触发杀毒误报。
- 主发布 ZIP 解压后的固定结构为：

```text
cdloader-VFS-v<版本>.exe
cdloader/
  cdloader-vfs-core.exe
  Python / DLL / PYD / VFS runtime 文件
  SHA256SUMS.txt
```

- 游戏根目录示例：

```text
G:\SteamLibrary\steamapps\common\Crimson Desert
```

- 外层 `cdloader-VFS-v<版本>.exe` 必须是最小透明启动器，只允许定位并启动 `cdloader/cdloader-vfs-core.exe`、转发全部参数、等待并返回退出码。外层不得解包、注入、释放文件或下载内容。
- 核心程序必须兼容位于游戏根目录的一级 `cdloader` 子目录，并从其父目录识别 `bin64/CrimsonDesert.exe`。
- 用户使用习惯保持为：把完整 ZIP 解压到游戏根目录，直接双击根目录的 `cdloader-VFS-v<版本>.exe`。不得提示用户只复制外层 EXE；相邻 `cdloader` 目录是必需内容。

### 每次发布必须生成的三份正式产物

1. GitHub 完整主程序包：

```text
dist_nuitka/cdloader-VFS-v<版本>.zip
```

2. Nexus Lite 主程序包（不含原生 VFS runtime）：

```text
dist_nuitka/cdloader-VFS-v<版本>-Nexus.zip
```

3. GitHub 场外 VFS runtime 包：

```text
dist_nuitka/vfs_runtime.zip
```

该包使用 `build_vfs_runtime_package.ps1` 生成，ZIP 内必须保留顶层 `vfs_runtime/` 目录。用户直接解压到游戏根目录后形成：

```text
Crimson Desert/
  cdloader-VFS-v<版本>.exe 或 Nexus Lite 目录版入口
  vfs_runtime/
    nppvfs_launcher.exe
    vfs_runtime.dll
```

原生 VFS 最小源码审查包仍需生成，但作为 Nexus 人工审核/源码公开辅助产物，不计入上述三份用户运行发布包：

```text
dist_nuitka/vfsDmoe-source-v<版本>.zip
```

Nexus Lite 固定使用 `build_cdloader_vfs_nuitka.ps1 -NexusLite` 生成，必须完全排除：

```text
cdloader/cdmm/private/vfs_runtime/
nppvfs_launcher.exe
vfs_runtime.dll
```

Nexus Lite 必须带 `VFS-RUNTIME-REQUIRED.txt`，让用户从官方 GitHub Release 手动下载 `vfs_runtime.zip` 并直接解压到游戏根目录，最终形成游戏根目录下的 `vfs_runtime/nppvfs_launcher.exe` 与 `vfs_runtime/vfs_runtime.dll`。加载器不得自动下载外部 runtime，避免形成 downloader/dropper 行为。GitHub 完整包仍可内置 runtime，不能被 Lite 构建覆盖。

2026-07-12 已实测并证伪 Nexus 单 EXE 方案：不内置 runtime 的 Nuitka `--onefile` 产物 `cdloader-VFS-v4-N.exe` 在 VirusTotal 仍为 `2/70`，其中 Elastic 为 `malicious (high confidence)`，Bkav 为 `W32.Malware.*`。根因是单文件自解包特征重新出现。后续不得把 `build_cdloader_vfs_nexus_single.ps1` 的实验产物作为 Nexus 正式包；Nexus 正式包继续使用已实测 `0/66` 的透明目录版 `cdloader-VFS-v<版本>-Nexus.zip`。

源码来自：

```text
T:\C++\vfsDmoe
```

源码审查包只保留：

- `src/vfs_launcher`
- `src/vfs_runtime`
- `src/vfs_test_app`
- `vfsDmoe.sln`
- Visual Studio `.vcxproj`
- `build.ps1` 与纯英文 `.bat` 包装器
- `.gitignore`、README 和源码审查说明

源码审查包必须排除：

- `bin/`、`build/`
- `logs/`、dump、缓存
- `mods/`、游戏文件、sandbox
- EXE、DLL、PDB、LIB、OBJ、PYD
- 旧 ZIP、7z、RAR 等嵌套压缩包
- 私密配置、凭据、API Key、Token

源码说明必须记录对应发布版 `nppvfs_launcher.exe`、`vfs_runtime.dll` 的 SHA-256 和打包时的源码 commit。源码包生成后，应从暂存目录实际执行一次 `Release|x64` 构建，要求 0 错误；然后再次确认最终源码 ZIP 中没有任何二进制或嵌套压缩包。

### 主程序发布验证

每次发布至少完成：

- PowerShell 脚本语法解析。
- `python -m pytest test -q`。
- 对改动 Python 文件执行 Ruff。
- `git diff --check`。
- 实际执行 `build_cdloader_vfs_nuitka.ps1`，不能只修改脚本。
- 检查完整主 ZIP 根部只有版本化外层 EXE 和 `cdloader` 目录；Nexus Lite 额外允许 `VFS-RUNTIME-REQUIRED.txt`。
- 检查 `SHA256SUMS.txt` 全部匹配，并覆盖外层 EXE。
- 检查外层和核心 EXE 的 FileVersion/ProductVersion 均来自 `version.txt`。
- 实际运行外层 EXE，确认能启动核心、转发退出码；在非游戏目录测试时应由核心正常提示缺少游戏根目录。
- 报告主 ZIP、外层 EXE、核心 EXE、原生 launcher 和 runtime 的 SHA-256。
- 明确记录 Authenticode 签名状态；未签名时不得声称已经通过可信发布者验证。

### VirusTotal 与 Nexus 上传规则

- 先把主程序 ZIP 单独上传 VirusTotal，记录报告 URL、SHA-256、检出数和具体引擎标签。
- 不承诺 `0` 检出或保证 Nexus 自动放行。VFS Hook、ASI 物化和原生 DLL 可能触发启发式扫描。
- 已验证 `v4` 主 ZIP 的结果为 `1/66`：只有 Elastic 报 `Malicious (moderate Confidence)`，其余 65 家未检出。这属于单引擎静态 ML 误报证据，但最终结论仍由 Nexus 人工审核决定。
- Nexus 默认上传 `cdloader-VFS-v<版本>-Nexus.zip`，不得上传内置 VFS runtime 的 GitHub 完整包。
- Nexus Lite ZIP 和源码 ZIP 必须作为两个独立文件上传。源码包建议放 `Optional Files` 或 `Miscellaneous Files`。
- 绝对禁止把 `vfsDmoe-source-v<版本>.zip` 塞进主程序 ZIP。Nexus 官方禁止嵌套压缩包，会再次触发拒绝或隔离。
- 已验证 v4 Nexus Lite ZIP 在移除原生 runtime、把 C# 外层换成原生 C++ x64 启动器后，VirusTotal 完整 ZIP 结果为 `0/66`。后续版本仍必须重新扫描，不能沿用 v4 结论。
- Nexus 对包含 EXE、DLL、PYD、Hook/runtime 的包可能自动隔离，即使 VirusTotal 只有 `1/66`。这不等于确认存在病毒，而是进入 moderator 人工审核队列。
- 文件被隔离后不要删除、替换或反复重新上传。Nexus 官方说明这样可能破坏文件版本历史并拖慢审核。
- 隔离后应立即准备英文审核邮件，收件人固定为 `support@nexusmods.com`，并提醒用户也可联系站内 moderator。

### Nexus 人工审核邮件模板

每次必须根据实际版本、模组编号、文件 API ID、VirusTotal URL、GitHub Release URL 和哈希填好内容，不要把占位符原样交给用户：

```text
Subject: False Positive Quarantine Review Request - Crimson Desert Mod #<MOD_ID>

Hello Nexus Mods Support,

My uploaded file for Crimson Desert mod #<MOD_ID> has been automatically quarantined.

Mod page:
<NEXUS_MOD_URL>

Quarantined file:
cdloader-VFS-v<VERSION>-Nexus.zip

File API information:
<NEXUS_FILE_API_URL>

VirusTotal result:
<VIRUSTOTAL_URL>

The VirusTotal result is <DETECTIONS>/<ENGINES>. Only <VENDOR> reports a generic static machine-learning detection with <CONFIDENCE> confidence. All other vendors report the file as undetected.

This is an open-source VFS mod loader. The Nexus package does not use a single-file self-extracting executable or UPX compression. Its small native executable only starts the application core stored in the adjacent cdloader directory. The security-sensitive native VFS runtime is not included in the Nexus package and is provided as a documented manual external dependency through the official GitHub release.

Source code and the matching release are available here:
<GITHUB_RELEASE_URL>

A separate minimal native VFS source package is also provided on the mod page for security review. The main archive includes SHA256SUMS.txt so every packaged file can be verified.

Main archive SHA256:
<MAIN_ZIP_SHA256>

Please manually review and unblock the quarantined file.

Thank you.
```

### 发布说明固定要点

- 使用说明必须采用 Nexus 富文本可直接粘贴的普通文本，不要默认给用户 Markdown 标记。
- 明确要求“完整解压 ZIP 到游戏根目录”，并展示 `G:\SteamLibrary\steamapps\common\Crimson Desert` 示例。
- 明确 `cdloader-VFS-v<版本>.exe` 和 `cdloader` 目录必须相邻，不能只移动 EXE。
- 模组目录固定说明为 `G:\SteamLibrary\steamapps\common\Crimson Desert\mods`。
- 使用其他管理器时，只允许下载、安装和排序；不得由其他管理器 mount 或启动游戏。
- 当前 VFS 版没有普通加载菜单；双击后会自动扫描、排序、合成、构建/复用 VFS 并启动游戏，不要再写 `select Load Mods`。
- 必须说明 Windows 重启后的第一次 Steam 冷启动两阶段行为：加载器先纯净启动，用户进入主菜单后正常退出，加载器再自动继续 VFS 启动。

### Dark Mode Map 发布版本约定

- `Dark Mode Map` / `worldmap_darkmode.cdmod` 的 Nexus 发布版本固定使用 `1.13.01`。
- 后续重新打包、更新描述或制作发布资料时继续使用 `1.13.01`，直到用户明确要求更改版本。

### Enemy Health Multiplier 成功创建记录（2026-07-13）

- 用户已在游戏内确认 `Enemy Health x5.cdmod` 生效。模组修改 `gamedata/buffinfo.pabgb` 中两条原生难度 Buff：普通敌人的 `BuffLevel_Difficulty`（key `1000276`）与 Boss 的 `BuffLevel_Difficulty_Boss`（key `1000277`）。
- 最大生命值项使用 BuffData tag `98`（`VaryStatMaxValueRateBuffData`），其中 `f00=0` 表示最大生命值；倍率值是百万分比增量，倍率 `N` 写入 `(N - 1) * 1_000_000`。因此 x2/x3/x4/x5 分别写 `1_000_000`、`2_000_000`、`3_000_000`、`4_000_000`。
- 原始记录只含简单（`leading_lookup=1`）与困难（`3`）的生命值项；必须克隆困难项并改为普通（`2`），同时增加 `buff_data_count`。记录长度变化后必须通过 JSON loader 的 PABGH offset 修复流程生成 companion `buffinfo.pabgh`，不能只替换 PABGB。
- 四个包均以 `legacy-byte-patch` `.cdmod` 封装。回读验证要按 `apply_byte_patches()` 与 `fixup_pabgh_after_inserts()` 应用，再检查普通敌人和 Boss 在 `1/2/3` 三档中都是目标值；2026-07-13 的 x2/x3/x4/x5 均通过该结构与索引验证，只有 x5 已记录游戏内实测。
- Nexus 发布默认一个 `.cdmod` 对应一个独立 ZIP 和一个文件条目。即使是互斥倍率也不默认合包；ZIP 根部只能包含该单个 `.cdmod`，分别计算 ZIP 和 `.cdmod` SHA-256。此前 x2/x3/x4/x5 的合包仅为用户手动处理的例外，不得作为后续默认。

### Persistent Enemy HP Bars with Values 成功记录（2026-07-14）

- 用户已实机确认 `Persistent HP Bars with Values` 基础版与增强版生效。两个包都是 `file-replacement` `.cdmod`，目标固定为原版 `0012` 中的 `ui/subtitletagview.html` 与 `ui/subtitletagview.css`；这不是 Format 3/semantic-patch，不能宣称同一 UI 文件内部可以字段级合并。
- 原始 `Persistent Enemy HP Bars` 的核心是保持 `UIGamePlayControlRootSubtitleTag` 的 HeadUp/HPGauge 显示，并解除原版 HP 控件的隐藏状态。扩展时必须保留这套持续显示生命周期，不能只看到数值出现就认为功能完成。
- 第一次 HP 数值实现把 `HPGauge` 改成 `CharacterStat.StatusGaugebarWithText`。该模板确实会通过 `.cpp-gauge-value` 与 `.cpp-gauge-max-value` 显示当前/最大生命值，但实机表现为原持续血条消失、数值短暂显示后淡出。原因是该模板带有另一套 show-layer 生命周期；后续不得再用它实现敌人持久血条数值。
- 正确实现是在 `SubtitleTagView` 内声明本地 `PersistentHPGauge` 组件：继续使用 `UIGamePlayControlCommon_StatGauge` 与 `statName="Hp"`，完整复用 `StatusProgressingGaugebar` 的 `StatusGaugeMaxValueBG`、`StatusGaugeProgressing`、`StatusGaugeCurrentValue` 和 `StatusGaugeCurrentValueEffect` 结构，并在同一个组件中加入 `.cpp-gauge-value` 与 `.cpp-gauge-max-value` 数值节点。`HPGauge` widget 指向 `SubtitleTagView.PersistentHPGauge` 后，血条与数值共享同一 Actor/Stat 绑定和持续显示生命周期。
- 增强版利用原生 `StatusGaugeProgressing` 状态显示变化残影：`.cpp-decrease` 使用白色受伤残影，`.cpp-increase` 使用绿色治疗残影；通过在 120px 主血条上叠加静态 25%、50%、75% 标记实现生命刻度。用户已确认持续血条、当前/最大 HP、残影与刻度均在游戏内生效。
- 原版 HTML 虽保留 `UIGameDebugDamageText`、`UIGameComboDamageText`、DamageDebug/Combo HeadUp 容器和动画，单纯移除 `!cpp-none` 后仍没有伤害数字。VFS state 与最终 `nppsa` 已证明修改文件被正确加载，因此失败点是正式游戏流程没有向该调试控件提供可用的单次伤害事件；不要再把“解除隐藏”当作纯 UI 伤害飘字方案。
- 两个正式变体互斥：基础版 v1.1 只含持久血条与当前/最大 HP；增强版 v2.0 额外包含受伤/治疗残影和三段刻度。其他替换 `subtitletagview.html/css` 的模组也属于完整文件冲突，只能按最终加载顺序保留一个版本。
- 可重复生成工具为 `tools/build_persistent_hp_values_mod.py`，支持 `--variant base` 与 `--variant enhanced`；局部回归测试为 `test/test_persistent_hp_values_mod.py`。生成后必须严格回读 manifest、replacement 目标、payload 大小/SHA-256，并由用户实机验证 UI 生命周期和布局。
- Nexus 页面采用一个模组、两个独立文件：每个 ZIP 根部只放对应的一个 `.cdmod`。发布文案必须按 `file-replacement` 真实能力说明完整 UI 资源、VFS 映射和同路径冲突，不能套用语义表格补丁的字段合并优势。

### 四把传奇长枪闪电效果成功记录（2026-07-14）

- 用户已实机确认四个雷电 `.cdmod` 生效：`AeserionSpear_Lightning.field.cdmod`、`GraspingMoon_Lightning.field.cdmod`、`DivergingMoon_Lightning.field.cdmod`、`CirclingMoon_Lightning.field.cdmod`。它们都是单一 `semantic-patch` 组件，目标为 `iteminfo.pabgb`，每包保留 14 条 field-level 操作；这证明当前 `format3_iteminfo_record_writer.py` 对该类武器特效字段可稳定生成游戏可用结果。
- 四把武器的真实 selector 分别为：Aeserion 同时涉及 `KuKu_Fire_staff_spear_TwoHandSpear`（key `1002176`）和 `Legendary_Dragon_TwoHandGiantSpear`（key `15700`）；Grasping Moon 为 `Legendary_Moonhead_TwoHandGiantSpear`（key `15702`）；Diverging Moon 为 `Legendary_Moonhead_01_TwoHandGiantSpear`（key `15706`）；Circling Moon 为 `Legendary_Moonhead_02_TwoHandGiantSpear`（key `15707`）。后续做同类属性包必须同时保留 key 与 string_key，不能只依赖名称或按武器类别泛匹配。
- 已验证的火焰到雷电映射为：`gimmick_info` 和 `docking_child_data.gimmick_info_key` 从火焰特效 ID `1001492` 改为雷电特效 ID `1001961`；`equip_passive_skill_list` 中 `level=3` 的技能从 `91105` 改为 `91101`；完整 docking 结构中的 `docking_tag_name_hash[0]` 从 `666382090` 改为 `3365725887`。这套映射来自已验证的 `RighteousVerdict_Lightning.field.cdmod`，不能凭效果名称猜测新的元素 ID。
- Aeserion 的原记录没有 docking optional，必须写入完整 `docking_child_data`，因此会同时替换特效 ID 与 docking tag hash；其余三把长枪已有 docking 结构，只写 `docking_child_data.gimmick_info_key`。完整 docking 插入仍必须走既有窄 writer 的 4 字节空 optional 占位替换与 PABGH 偏移修复路径，不能改成任意结构拼接。
- 单把武器的 `gimmick_info` 与 `docking_child_data.gimmick_info_key` 都是单值字段；火焰包和雷电包同时启用会修改相同字段，最终仅由 `.cdloader/load_order.json` 中靠下的包覆盖生效。安装说明必须要求玩家对同一把武器只启用一种元素版本。`equip_passive_skill_list` 的序列化虽支持多个被动条目，但“多元素被动是否可在游戏内叠加”尚未实测，不能宣传多属性共存或随机元素效果。
- `tools/build_lightning_spear_mods.py` 可从四个火焰源包确定性生成上述四个雷电包，并会严格校验只出现预期的 3 或 4 次字段替换；生成后必须用 `load_cdmod_package()` 回读，确认每包 14 条操作。Nexus 发布资料由 `tools/prepare_lightning_spear_nexus_release.py` 生成到 `nexusmods/11-lightning-spears-1.13.01-cdmod`：四个独立 ZIP，各自根部仅含一个版本 `1.13.01` 的 `.cdmod`，并已附 SHA-256、指定封面和中英 BBCode。
- 发布页面须明确署名：本独立闪电 `.cdmod` 重构版受原作者 `GamingModsOn` 的页面 `https://www.nexusmods.com/crimsondesert/mods/3105` 启发；不是原始上传，也不暗示原作者授权或背书。
- 用户后续已实机确认组合包 `All_TwoHandSpears_Lightning.field.cdmod` 生效。最终稳定配方不能从大剑雷电包推断，必须逐项包含四个已验证长枪包的共同结构：效果供体 `1002176 / KuKu_Fire_staff_spear_TwoHandSpear` 只写三段 `cooltime=1000`；目标长枪写雷电 gimmick `1001961`、被动 `91101/level 3`、`item_charge_type=0`、三段最大充能 `10` 和 `respawn=0`。不得再套用 Righteous Verdict 大剑的最大充能 `3`。
- Aeserion `15700 / Legendary_Dragon_TwoHandGiantSpear` 是已验证的完整 docking 插入特例；Grasping/Diverging/Circling 分别为 `15702/15706/15707`，原版已有 docking，只修改 `docking_child_data.gimmick_info_key`。组合包生成后，这四条最终 ItemInfo record 必须与四个 Nexus 独立成品逐字节一致；2026-07-14 已完成 `byte_equal=True` 验证。
- 用户实机确认该游戏一把武器只能保留一种属性效果，后写属性会覆盖原属性。因此后续 V2 不得修改原版已经带火焰、雷电、风力、EMP、铋元素、激光等特殊效果的武器；只允许给原版没有特殊效果的明确武器添加雷电属性。不能再把“所有双手长枪”作为无条件批量目标。
- 中文 PALOC 反查确认：`西德蒙长枪` 对应 `310008 / Kephilray_TwoHandSpear`；`黄金先锋` 对应 `265011 / Luon_TwoHandAlebard`，内部属于双手战戟而不是 `TwoHandSpear`。两条原版记录均无 docking、被动列表为空，writer 定位出的 docking 前置区与 Aeserion 一样是连续 34 字节空占位。
- V2 首批测试包为 `NoSpecialEffect_Polearms_Lightning_V2.field.cdmod`，只包含西德蒙长枪与黄金先锋，直接复制 Aeserion 已验证的 3 项供体 + 11 项目标配方并替换明确 selector；V1 与 V2 联合内存构建无错误，最终两条记录均为雷电 gimmick `1001961`、被动 `91101/3`、最大充能 `[10,10,10]`，6508 条 PABGH 边界完整。该 V2 目前只有构建级验证，必须等用户实机确认后才能标记为生效或继续扩展到其他无特效长柄武器。
- 2026-07-14 后续实机结论修正：无 docking 的 `gimmick_info=1001876` 方案只有雷属性词条，武器雷电特效和命中雷伤都不生效，禁止继续使用。恢复 `gimmick_info=1001961` 与完整 docking 后，Aeserion 的 `docking_tag_name_hash[0]=3365725887` 仍只产生词条/视觉；改为原生 `Marni_MachineKnight_TwoHandSpear` 使用的双手长枪标签 `666382090` 后，用户确认西德蒙长枪的武器雷电特效和命中雷伤均正常生效。
- 同一 test3 配方在黄金先锋 `265011 / Luon_TwoHandAlebard` 上仍不产生命中雷伤，说明战戟 `equip_type_info=3456689820` 不能直接复用双手长枪命中标签。后续批量扩展只允许 `equip_type_info=2914941932` 且名称以 `TwoHandSpear`/`TwoHandGiantSpear` 结尾的长枪；战戟必须排除，直到找到并实机验证战戟自己的原生特效供体。
- 2026-07-14 用户进一步实机确认：按上述结构规则筛出的 23 把原版无特殊效果长枪全部生效，均同时具备雷属性词条、武器雷电特效和命中雷属性伤害。正式 V2 只包含这 23 把长枪，排除黄金先锋与所有 `Alebard`，也排除原版已有火焰、雷电、风力、EMP、铋等特殊效果的长枪。旧 `All_TwoHandSpears_Lightning` V1 不再保留或发布；Nexus 正式标题使用 `All Non-Elemental Spears - Lightning Effects`，版本为 `2.0`。
- 2026-07-14 用户实机确认火焰变体 `NoSpecialEffect_Spears_Flame_V2.field.cdmod` 同样对全部 23 把目标长枪生效，包含火焰属性词条、武器火焰特效和命中火焰伤害。正式配方为 `gimmick_info=1001492`、`equip_passive_skill_list=91105/level 3`、`docking_tag_name_hash[0]=666382090`，其余冷却、充能和生命周期字段与已验证雷电版一致。火焰版和雷电版修改相同 ItemInfo 字段，属于互斥变体；同时启用时只由最终加载顺序靠后的一个生效。Nexus 正式标题使用 `All Non-Elemental Spears - Flame Effects`，版本为 `2.0`。

## 2026-07-17 性转角色女巫脸与女性装备替换实机基线

### 五女巫脸型与发型

- 用户已实机确认 `ZZ - Full Human Female Five Witch Faces and Hairstyles 2-3-4-5-7-1.5-test.cdmod` 的五张脸与五个发型全部可正常使用。映射为：`2=Areciel/0139/0504`、`3=Bari/0143/0007_01`、`4=Elowen/0141/0006_04`、`5=Lyselia/0019/0505`、`7=White Crow/0046/0018`。
- 成功方案必须复制完整 Human Female standalone，并同步等长修改 `meshparam_example_damian.xml` 与 `meshparam_example_kliff.xml`。脸部 `ParamDesc Index=1` 的 skeleton、mesh、icon 三处身份一起替换；发型 `ParamDesc Index=2` 采用目标槽与女巫原生槽成对交换，保持 MeshSet 数量和 Index 唯一。
- 完整重打包必须保持原 PAMT、PAZ 总长度、其他 entry offset 和压缩 entry 的 `comp_size`；LZ4 尺寸需要精确恢复时，只允许调整已有 XML 注释中的安全 ASCII 填充。不得重新回退到小型 standalone 六资源覆盖、单 PABC/copy-entry 或只修改一份 meshparam。
- 理发师/角色创建器首次预览不是最终验收。必须应用目标脸后保存，再返回主菜单重新加载存档。此前多轮“无效果”是漏掉保存重载步骤；后续脸型、发型、妆容都要分别验证首次应用、保存重载和游戏重启。
- 原生 27 号 K-Makeup 能稳定重载，不代表妆容可以跨脸位身份复制。把 27 号资源伪装成 1 号脸会在重载后掉妆；当前五女巫 1.5 是无妆容稳定基线，妆容持久化仍需独立结论。

### 女性战场之光头饰替换白鸦主帽

- 性转链必须分层理解：`CharacterCreatorHead.asi` 提供 Human Female 外观链；`Equip Everything V6.cdmod` 让男性克里夫能穿女性达米安装备；最终实例化仍读取女性 `character/cd_phw_00_hel_00_0151.prefab`。不要因为玩家底层是男性就猜测需要改男性战场之光 Prefab。
- `Demenissian Clothing (Necklace and Mask)` 的稳定样本证明：装备替换应保留目标 Prefab 的组件布局、UID、骨骼 socket 和文件结构，只改内部女性 PAC 引用。完整复制来源 Prefab 不是等价操作。
- 用户已实机确认 `ZZZ - Light of the Battlefield White Crow Main Hat Structural-0.5-test.cdmod` 成功。它以原版 1,941 字节 `0151.prefab` 为基底，只把主 PAC 路径末尾 `0151` 等长改为 `0164`；文件仅变化 2 字节，目标仍只有 1 个模型引用，不强塞 White Crow 的第二个 `0164_sub01` 组件。
- 成功日志 `Launcher_2026_07_17_14_20_42_30804.log` 达到 `(12/12)`、三次 `End Load SaveSlot105` 并正常 `Terminate`。生成工具为 `tools/build_battlefield_light_white_crow_hat_swap.py`，会固定校验 1.13.01 原版 0151/0164 SHA、路径唯一性、等长输出和模型引用数量。
- 详细经验统一位于 `docs/CrimsonDesert-角色创建器女巫脸与女性装备替换经验.md`；原脸型研究记录位于 `docs/角色创建器女巫脸替换研究记录.md`。

### 新增高风险规则：White Crow 完整 Prefab 复制

风险 ID / 名称：`white-crow-full-prefab-copy` / White Crow 0164 完整 Prefab 覆盖战场之光 0151  
适用游戏版本：Crimson Desert `1.13.01`  
最终目标路径：`character/cd_phw_00_hel_00_0151.prefab`  
危险操作或字节签名：`resource-transform copy-entry` 的来源精确为 `character/cd_phw_00_hel_00_0164.prefab`；或 `file-replacement` 声明 payload SHA-256 为完整原版 0164 明文 `8f51daa6f36a246076b3bf3b36fef7c4c200dea14d9b2cb44fa57a2414252dc6`  
失败阶段与稳定复现次数：完整复制包在 `(12/12) + End Load SaveSlot105` 后同秒崩溃，已确认 1 次  
失败日志 / WER / Dump 证据：`Launcher_2026_07_17_13_28_18_33084.log`  
对照成功证据：0.5 等长主 PAC 路径替换，`Launcher_2026_07_17_14_20_42_30804.log` 正常完成三次 SaveSlot 并退出  
已验证安全替代方案：保留 0151 目标结构，只把 `...0151.pac` 等长改为 `...0164.pac`  
匹配边界与明确不包含项：不得泛化到所有 0151 修改、所有头饰 Prefab 或其他来源复制；0.5 payload SHA `27f1ea...` 必须不误报  
回归测试：精确 copy-entry 命中 / 0.5 风格 file-replacement 不误报 / CMD 红字入口沿用统一路由

### 新增高风险规则：条件装配表重复 standalone 最终路径

风险 ID / 名称：`duplicate-conditional-part-prefab-table` / 条件装配表重复注册  
适用游戏版本：Crimson Desert `1.13.01`  
最终目标路径：`character/descriptors/conditionalpartprefab/conditionalpartprefab_transmog.xml`  
危险操作或字节签名：两个及以上活动 standalone PAMT 同时解析到该最终路径；不能只按 PAMT SHA 判断，因为完整 179+1 表和单 Condition 0.4 都会失败  
失败阶段与稳定复现次数：数据加载 `2/12`，稳定复现 3 次  
失败日志 / WER / Dump 证据：`Launcher_2026_07_17_13_46_47_29132.log`、`Launcher_2026_07_17_13_47_23_23964.log`、`Launcher_2026_07_17_14_12_14_25824.log`；均报原表第 21 行 `cd_m0001_00_so_phm_ub_31090` 的 `SourcePartPrefab` 重复  
对照成功证据：只保留原 `N20260710213328.cdmod` 条件表并改用 0.5 Prefab 路径方案后，`Launcher_2026_07_17_14_20_42_30804.log` 成功  
已验证安全替代方案：构建前合成为单一条件表且 PAPGT 只注册一次，或绕开条件表使用目标 Prefab 内等长 PAC 路径替换  
匹配边界与明确不包含项：只匹配上述精确 XML 最终路径，不泛化到其他 standalone 同路径资源；默认红字告警，不自动禁用  
回归测试：不同 PAMT/PAZ 的同最终条件表准确命中 / 其他 XML 最终路径不套用 2/12 结论 / VFS 与普通控制台继续使用 standalone 红字路由

## 2026-07-20 0129 白色尖帽回收与完整材质链实机基线

- 用户提供的旧版 `V:\红色沙漠\0.pamt + 1.paz` 已确认配对有效；旧 `1.paz` 中 `character/cd_phw_00_hel_0129.pac` 与当前 1.14 同路径 PAC 大小和 SHA-256 完全一致。制作组没有删除白帽主网格，之前是资源定位错误。
- 该资源没有独立 `ItemInfo` 装备记录或本地化显示名，准确称呼应为“0129 白色尖帽 / White Pointed Hat 0129”。波伦皮革头盔只是旧 `Demenissian Clothing` 模组的替换目标，不是白帽原名。
- 用户已实机确认 0129 白帽替换战场之光 `character/cd_phw_00_hel_00_0151.prefab` 成功，白色布料材质、帽型和装饰正常。安全方案保留 1,941 字节目标 Prefab，只把唯一主 PAC 等长改为私有 `...cd_phw_00_hel_00_9129.pac`。
- 私有 PAC 必须同时提供同 basename 的 `character/modelproperty/.../cd_phw_00_hel_00_9129.pac_xml` 和 `character/bin__/meshphysics/.../cd_phw_00_hel_00_9129.hkx`；只提供 PAC 会让布料回退成强镜面高光的塑料材质。原生 0129 DDS 继续复用，不需要复制。
- 暗黑执行者板金头盔已反查为 `Executioner of Darkness Plate Helm / Demian_PlateArmor_Helm_XI / item 1000842`，女性目标是 `character/cd_phw_00_hel_00_0169.prefab`，同为 1,941 字节单主 PAC 结构。用户已实机确认白帽外观和完整材质链正常。
- 共用生成器为 `tools/build_battlefield_light_white_pointed_hat.py`，目标参数是 `--target battlefield-light` 与 `--target dark-executor`。详细证据位于 `docs/CrimsonDesert-0129白色尖帽回收与完整材质链经验.md`。

## 2026-07-21 0141 舞者服披风替换大地荣誉皮制披风实机基线

- 用户已实机确认独立 `.cdmod` 成功：目标是“大地荣誉皮制披风”女性 `character/cd_phw_00_cloak_00_0163_t.prefab`，来源是原 `Demenissian Clothing` 清单标注为 `Dancer Clothes` 的原生 `character/model/1_pc/2_phw/armor/19_cloak/cd_phw_00_cloak_00_0141.pac`。
- `0141` 没有独立 `ItemInfo` 物品记录或可证实的官方本地化名称，准确资源简称为“0141 舞者服披风”。大地荣誉皮制披风是替换目标，不是 `0141` 原名；不得把资源简称宣传成游戏内可单独获得的官方物品。
- 当前 1.14 已原生提供 `0141` 的 PAC、同 basename `pac_xml` 与 HKX，且来源 Prefab 只有一个模型引用。因此无需像 0129 白帽那样创建私有别名或重复携带完整材质链，只需路由目标 Prefab。
- 原 loose 模组的 `0163_t.prefab` 为 1,826 字节；当前原版目标为 1,800 字节。安全包禁止直接复制旧 Prefab，应以当前原版 1,800 字节目标为基底，把唯一主 PAC 从 `...0163.pac` 等长改为 `...0141.pac`。最终仍为 1,800 字节、单模型引用，仅变化 2 个数字字节，用户已确认外观生效。
- 生成器为 `tools/build_earths_honor_dancer_cloak_mod.py`；独立 `.cdmod` SHA-256 为 `54617B8F00266B420679CA5FAC8A653D1F0F5516743463B2B08917F52F630F10`。第 25 号中文发布 ZIP 只包含 `.cdmod + 1600x900 模组封面.jpg`，SHA-256 为 `1E63FD9414A9E942158B0E63D23D85890544F729A4ACDE3269369CE00FE4EF5C`。
- 后续从旧服装模组回收隐藏/非独立装备资源时，统一沿“旧目标 Prefab 反查来源 PAC -> 当前 PAMT 核验完整原生链 -> 当前目标结构内唯一等长路由 -> 正式 cdmod 回读 -> 用户实机确认”推进。详细证据位于 `docs/CrimsonDesert-0141舞者服披风资源定位与安全替换经验.md`。

## 2026-07-21 CC 女性脸 0271 黑色面罩替换白色丝巾实机基线

- 用户已实机确认 `Alternative Mask - White Silk-1.14.cdmod` 内部版本 `1.14.2` 成功：白色丝巾正常出现，并适配通过 `CharacterCreatorHead.asi` 性转后的女性脸。成品 SHA-256 为 `2193F4B0CBB2EDAA63CF290C3B1A6F0D99DF4705D93CFBE26B73593C77095C4D`。
- 关键结论：CC/ASI 让男性 Kliff 使用女性外观链，不代表装备选择也必然走女性 Prefab。0271 面罩实机仍可能选择男性 `character/bin__/prefab/1_pc/01_phm/armor/20_mask/cd_phm_00_mask_00_0271_a.prefab`；只覆盖女性 `cd_phw_01_mask_0271.pac` 时，即使最终 `nppsa` 字节完全正确且无后加载覆盖，游戏仍会显示原面罩。
- 1.14 同时存在男性与女性 0271 面罩 Prefab，二者均为 1,888 字节单组件结构。最终成功包保留两份 Prefab 的组件、UID、socket 和长度，把内部唯一 PAC 路径都等长改为女性 `character/model/1_pc/2_phw/armor/20_mask/cd_phw_01_mask_0248.pac`。男性分支也必须使用女性网格，不能因为装备身份为男性就继续加载男性 0248。
- 已证伪的中间方案是男女 Prefab 分别路由到各自性别 0248。丝巾会出现，但男性 `cd_phm_00_mask_0248.pac` 套在 CC 女性脸上会发生明显鼻子/嘴部穿模。男性 PAC 包围盒约为 `0.215787 x 0.239038 x 0.330773`，女性 PAC 约为 `0.204348 x 0.269943 x 0.298084`；“资源出现”和“女性脸贴合”必须分别验收。
- 当前 1.14 的 0248 默认材质已经从旧版白色 `Silk` 变为深色 `Cotton`，且 PAC_XML 从单一 ModelProperty 扩展为多材质结构。最终包不复制过期完整 XML，而是在当前女性 0248 PAC_XML 中保留全部结构，仅恢复默认 `ModelProperty Index=0` 的白色 tint、染色参数与 `_clothCategory=Silk`。
- 成功包只有 3 个 `file-replacement`：男性 0271 Prefab、女性 0271 Prefab、女性 0248 PAC_XML。生成工具为 `tools/build_alternative_white_silk_mod.py`；生成时固定校验当前 1.14 原版 SHA、Prefab 旧路径唯一性、等长路径替换、正式 cdmod 回读和模拟 overlay 构建。
- 该结论不能泛化为所有性转装备都需要双分支。战场之光已经实机证明最终只走女性 0151 Prefab。后续必须针对具体物品反查实际 Prefab/PAC 引用，并独立验收资源路由、性别网格贴合、材质外观及保存/重载。
- 完整排障与制作经验已写入 `docs/CrimsonDesert-角色创建器女巫脸与女性装备替换经验.md`；红沙技能维护边界已同步到 `.codex/skills/crimson-desert-mod-loader/references/loader-principles.md` 第 15 节。

## 2026-07-27 双持单手剑背挂与武器 Socket 调整实机成功基线

- 用户已实机确认最终 `Dual One-Hand Swords Back Carry - CC Compatible-1.15.31.cdmod` 没有问题。可见武器身份已由单变量测试确认：Spine0 控制黑剑，Spine1 控制金剑；不得再根据 R/L、左右手或 socket 名称反向推断。
- 最终黑剑 Spine0 为 Rotation `-0.69348 -0.21269 0.236657 -0.64641`、Translation `-0.11000 -0.04000 -0.636000`；最终金剑 Spine1 为 Rotation `-0.69640 -0.20417 0.227599 -0.64926`、Translation `-0.14000 -0.07500 -0.570000`。
- 当前 1.15 成功包动态覆盖 3 个角色描述、2 个 body socket、7 个 PHM/PHW 单手剑 sidecar、281 个含挂载字段的单手剑 Prefab 和 9 个 PHM 双持动作，共 302 个 resource-transform 操作。必须继续使用当前原版上的唯一等长窄替换，禁止复制旧完整 Prefab/PAA/PAZ/PAMT/meta。
- 同类武器挂载必须按“单链身份标定 -> 单轴方向标定 -> 锁定满意武器 -> 微调另一链 -> 最终 nppsa 全目标回读”推进。包内参数正确不能代替实机视觉结论；最终必须核对 7/7 sidecar、281/281 Prefab 和旧参数残留 0。
- 本链实机确认 Spine1 Z 正向增量让金剑向下；Y 改变前后层次并影响横向投影；X 用于横向间距。该方向绑定当前父骨和旋转，不能泛化到其他武器类型或新挂载链。
- 生成器为 `tools/build_dual_onehand_swords_back_carry_mod.py`。最终 SHA-256 为 `5F7D510CB4A5BB705E0A7C8F0EDCFBB502D6C2EC97BEEB430466D0EE9914532C`，Nexus 归档位于 `nexusmods/27-dual-one-hand-swords-back-carry-1.15.31-cdmod/`。完整经验见 `docs/CrimsonDesert-双持单手剑背挂与武器Socket调整实机经验.md`，逐版记录见 `docs/双持单手剑背挂调整记录.md`；红沙技能已同步到第 17 节。
- 2026-07-27 已纠正兼容性误判：旧 Witcher loose 与 Hospade 同路径覆盖是真实的，但“发生覆盖”不能直接定性为剑鞘位置错误的根因。最终 `1.15.31` 的 resource-transform 会叠加到 loose base，用户已实机确认原 Hospade loose 与最终主包共存正常；Hospade 专用转换包不是最终双剑模组依赖。
- Human Female 对照第一次取到的同名文件 SHA-256 为 `7A3DA58C...E4CAE`，manifest 已含 `witcher_swords_placement=1.15.6`，不是干净原包。用户随后换回第 23 号发布 ZIP 内 SHA-256 为 `4F89F139...E0E1C4`、不含挂载标记的真正干净包；在 VFS 指纹 `fed58b01530c...` 下实机确认最终剑鞘位置仍生效。因此 Human Female `1.15.10` 专用重打包也不是最终双剑模组依赖。以后不得按文件名判断“原版”，必须核对完整 SHA、大小和 manifest/source 标记。
- 干净 Human Female `0043` 的 PHW description 确实不含双剑 Spine0/Spine1 路由，但它不含 PHM player/Kliff description、281 个单手剑 Prefab 或 7 个 weapon sidecar。性转外观链不等于装备/武器装配链；最终 nppsa 的 PHM Kliff 路由、Prefab 和 child socket 仍在，所以“PHW 同名文件被覆盖”与“最终剑鞘功能失效”不能画等号。
- 同路径兼容性结论必须分级：路径重叠只能称潜在冲突，最终字节赢家只能证明发生覆盖；至少经过固定主包与环境、只切换一个待测模组的实机 A/B，才能说影响功能；恢复后消失并重新引入稳定复现，才能称根因。管线从 loose/完整替换改为 resource-transform 后，必须重新审计旧兼容包是否仍有必要。
- 详细误判时间线、反证和发布前依赖复审清单位于 `docs/CrimsonDesert-双持武器挂载兼容性误判复盘.md`。

## 2026-07-27 双手大剑右腰挂与收剑动作同步实机成功基线

- 用户已实机确认 `Two-Handed Swords Hip Carry - CC Compatible-1.15.11.cdmod` 成功：右腰静态位置、方向和角度正确，PHW `rpsd_in/out` 拔刀/收刀姿态命中，收剑时剑体不再提前秒回，而是在手臂动作末段同步回到右腰。
- 成功链必须分成四层维护：3 份 PHM/PHW player description、PHM body socket、5 份 PHM/PHW weapon sidecar 和 57 份双手剑 Prefab 负责静态装配；PAA/LOD 负责可见姿态；`0010` 的配对 `PAA_metabin` 负责动作元数据；活动 PAAC 节点的 PartInOut transition threshold 负责剑体换挂点时刻。
- `1.15.9` 修改 motion 附近未知 `0.1667/0.3333` 浮点到 `1.7667` 实机无效；`1.15.10` 移除 `Visible="Out/In"` 实机也无效。以后不得根据字段邻近、数值像秒数或 XML 属性语义直接宣称因果。
- 当前 1.15 原版 `actionchart/bin__/upperaction/1_pc/1_phm/basic_upper_weaponin.paac` 大小 149,283，SHA-256 为 `8e55e758a917dc10aa69c35245220ddab46168b2fe27891561ef79b2b9183525`。后半事件实例记录内 `+0xd4` 的 `u16` 是 part 索引，transition 的 `target * 4` 可回查图缓冲区；`target=1231` 属于 part 4，不能修改，真正的双手剑 part 7 是 `target=1245`。
- 成功版只把 `0x2b1f`、`0x2f50` 两条完整 `<ffII>` transition 的 threshold 从 `0.53333336` 改为 `1.5`，保留 `end=-1.0`、`target=1245`、`sequence=4`。最终 `nppsa` 与原版相比只有 6 个实际字节变化，用户实机确认收剑同步成功。
- 成品 SHA-256 为 `74CA38C24689770947E1AB1952BE17C2E0C2BCFC9ADF9F3F9E537442F4917807`；VFS 成功构建指纹为 `c4ac9ad635069a9b2721e4d6225758fe54a5a6cbda9ebecb22fee3f57044a24e`。生成器为 `tools/build_twohand_sword_hip_carry_mod.py`，完整逐版证据位于 `docs/CrimsonDesert-双手大剑腰挂与双持单手剑资源链区分.md`，技能基线位于 `.codex/skills/crimson-desert-mod-loader/references/loader-principles.md` 第 18 节。
- 同类武器腰挂/背挂、性转动作和拔刀/收刀同步以后统一复用“静态链锁定 -> PAAC 实际引用选 PAA/LOD/metabin -> event part 归属 -> transition target 图回查 -> SHA/完整旧 tuple/允许变化字节严格校验 -> BuildOnly 最终 nppsa 回读 -> 分层实机验收”流程。可复用的是定位与验证方法，不是当前偏移；游戏更新或更换武器、动作状态后必须重新定位。
- 当前实机成功范围是普通站立收剑同步。坐姿、警戒和骑马的 PartInOut 时序仍需各自实机确认，不能直接从站立结果外推。

## 2026-08-10 No Fall Damage / BuffInfo 1.17 迁移实机基线

- `No-Fall-Damage.field.json` v4.4 在游戏 `1.0.0.2330` 中会被正常扫描并列入加载顺序，但旧加载器会把 `buffinfo.pabgb` 的 2 条核心 intent 全部跳过；“已识别为 Format 3”不能当作已生效。
- 1.17 的两个目标记录保持原长度、10 级边界、公共 payload 和 variant tail 不变，只因枚举前方成员删除发生 tag 前移：`ChangeBuffLevelBuffData 80 -> 79`，tail 8 字节、`f01=u32@+4`；`AddPercentInGameContentsBuffData 104 -> 103`，tail 17 字节、`f01=u64@+1`。
- 新 tag 支持必须由当前低编号 vanilla `buffinfo.pabgb/.pabgh` 和旧版留存表对比得出，并逐项行走到记录 `min_level_offset` 精确闭合；禁止按模组名、key 或全局绝对 offset 硬编码，也禁止假设后续版本继续递减。
- 修复后真实表为 `buffinfo 2 changes / 0 skipped`、`iteminfo 53 intents -> 22 merged changes / 0 skipped`。最终 `nppgen` 回读 key `1000185` 第 10 级值为 `100000`，key `1000190` 为 `100000000000`；最终 `nppv3_iteminfo` 重放 53 条 intent 均为目标字节已存在。
- 用户通过 `.\run_cdmm_vfs.bat -AllowMissingTargets -NoBuildVfsDemo -KeepRunning` 实机确认无跌落伤害。详细迁移流程与未来更新边界位于 `.codex/skills/crimson-desert-mod-loader/references/no-fall-damage-mod.md`。

## 2026-08-15 Equip Everything V9 / ItemInfo 1.18 迁移基线

- 游戏更新到 `CrimsonDesert.exe 1.0.0.2443`（1.18）后，`Equip Everything V9.0`（游戏 `mods/Equip Everything V8.0.json`，modinfo title 为 `Equip All V9.0`）的 2381 条 `iteminfo.pabgb / prefab_data_list` intent 全部跳过：`Format 3 bridge 299 个生成补丁 / 跳过 2381 个 intent`，警告 `prefab_data_list 新增元素缺少 tag_name_hash` 与 `legacy fallback 未生效：未定位到 DMM V3 legacy prefab_data_list 尾部块`。
- 根因不是容器或 intent：V9 JSON 与 1.18 前备份 `Equip Everything V8.0.pre_1_18_union.bak` 的 2381 个 key 完全一致（仅 2 个 new 值不同）。是 1.18 更新同时改变了 ItemInfo 的两个二进制结构：
  1. 原版记录中 `drop_default_data` 与 `prefab_data_list` 之间新增了约 1256 字节字段（原生 schema walk 因此把 prefab 定位到 `0x1BE` 读到空列表，真实 prefab 在记录尾部 `0x6A6`）；
  2. **每个 legacy PrefabData 元素在 `tribe_gender_list` 之后新增 u32 unk 字段（实测恒为 `0xEAC5E173`）**，尾部从 3 字节（craft/use/type）变成 7 字节（unk + 3×u8）。旧 `_consume_legacy_prefab_data_list` 按 3 字节尾部消费，元素边界错位 4 字节/元素，导致 `_locate_legacy_prefab_data_list` 返回 None。
- 修复落在 `services/format3_iteminfo_writer.py`：`_consume_legacy_prefab_data_list` 先按新版 7 字节尾部消费（校验 unk == 0xEAC5E173），失败回退旧版 3 字节；`_pack_legacy_prefab_data_list` 默认输出 1.18 新版 7 字节尾部（`include_unk_tail=True`）；`_build_legacy_prefab_list_change` 以原版块实际尾部形态决定打包，避免新旧字节错配。
- 修复后真实冷构建：`Format 3 bridge 处理 1 个模组、4 个目标，2680 个生成补丁`（= 原 299 + 2381），0 skipped；`nppv3_iteminfo` 包正常生成，PABGH 6573 个 key 边界完整；抽样 7 个目标记录（1000231/1000232/1000233/1001273/2113183/1004821/1004818）的 legacy prefab 块均能定位，意图 prefab hash 全部出现在最终块中。
- 新增回归测试：`test_iteminfo_prefab_legacy_tail_supports_118_unk_field`（1.18 7 字节尾部定位+替换+保留 unk 标记）与 `test_iteminfo_prefab_legacy_tail_falls_back_to_old_3byte_tail`（旧版 3 字节尾部兼容）。完整 `pytest test` 471 passed、`ruff check .` 通过。
- 后续游戏更新必须重新确认 legacy PrefabData 元素尾部字节数（3 vs 7）与 unk 标记值；不得假设 unk 恒为 `0xEAC5E173` 或尾部永远 7 字节。

## 2026-08-17 Expanded Vendor Inventory V3 / Dye Addon Format 3 基线

- 当前加载器已为 `Expanded Vendor Inventory Rebuilt V3 1.0` 与其 `Dye Addon` 增加四张表的窄 writer。主模组包含 StoreInfo 2882 条 intent、ItemInfo 2127 条 intent；addon 包含 DyeColorGroupInfo 220 条 `array_append` 与 NpcInfo 90 条 `array_append`。
- Format 3 parser 需要把 `array_append` 导出的 `value` 与普通 `set` 的 `new` 统一到 `Format3Intent.new`；两者同时存在时仍以 `new` 为准。禁止让各 writer 自行维护两套输入字段名。
- StoreInfo 1.18 的 StockData 固定头为 114 字节，无 sub_data 记录为 119 字节，有 sub_data 为 132 字节；`+42` 恒为 1、`+43` 为商品 key、`+51` 为 discriminator。当前真实模组同时包含 Disc0 与 Disc3，writer 必须保留完整记录字段和 sub_data，不能退回仅支持 Disc0 的旧解析器。
- 普通商店 stock count 常在 payload `+45`，贡献商店头部更长；当前 writer 通过 count 后的 119/132 字节记录链动态定位，不按商店名称或固定绝对偏移硬编码。支持字段限定为 `stock_data_list`、`buyable_stock_count`、`sellable_stock_count`、`exchange_item_info_for_buy` 与 `stock_data_list[N].raw_c`。
- 多个商店大幅增长时必须先在内存中合成完整 `storeinfo.pabgb`，再一次性生成整表 body change 和完整 PABGH change。禁止逐 entry 依赖 legacy `DYNAMIC_ENTRY_RELOCATION_WINDOW=512`，否则后续商店会因累计漂移跳过。
- ItemInfo 价格支持限定为基础 `price_list[N]` 和 `enchant_data_list[N].buy_price_list[M]` 的 `key`、`price.price`、`price.item_info_wrapper`。ItemPriceInfo 当前布局为 `key:u32 + price:u64 + sym_no:u32 + wrapper:u32`；强化价格必须从可完整解析的 EnchantData 序列定位，无强化记录只接受唯一合法价格数组，不做跨记录模糊扫描。
- DyeColorGroupInfo 每个追加元素为两个 u32（`texture_lookup/raw_color`）。1.18 NpcInfo
  必须先消费旧三个 LocalizableString 后新增的 `u32 + 2*LocalizableString + u32`，
  再定位 `DyeColorGroupData`（8 字节）和 `DyeTextureSetData`（6 字节），并保留其后的
  `CArray<u16>` 尾数组；禁止用“从 entry 尾部扫描可闭合 count”的近似算法。原版
  `dye_target_key=0` 是合法值，不得擅自改成 owner key。
- 使用当前 1.18 原版四张表的最终验证：`storeinfo 2882 intents -> 2 changes / 0 skipped`、`iteminfo 2127 -> 73 / 0`、`dyecolorgroupinfo 220 -> 2 / 0`、`npcinfo 90 -> 2 / 0`，总计 5319 intents、79 changes、0 skipped。ItemInfo 2127 个目标字段逐项回读均为 1且表长/PABGH 不变；StoreInfo 重建后 437 条 PABGH 边界和 23 个目标商店均可重新解析，119/132 字节记录、Disc0/Disc3、sub_data 与目标字段均完成往返验证。
- 上述结论是加载器解析、写入、PABGH 和最终字段回读的字节级基线，不等于游戏内商店库存、价格或染色功能已经实测。未取得用户实机结果前不得写成“游戏内已确认生效”。
- 真实 VFS 冷构建还暴露了统一语义计划层的集成边界：原 JSON 对同一商店连续执行大量 `stock_data_list array_append`，再对追加后的 `stock_data_list[N].raw_c` 执行 `set`。计划层不得把同坐标 append 当成后写覆盖而只保留最后一条，也不得把这个同包有序序列误判为跨模组父子冲突。
- `cdmod_build_plan` schema 2 会把同坐标 legacy `array_append` 按输入顺序完整收集，`cdmod_format3_bridge` version 2 再逐条还原给 table writer。父子豁免严格限定为同一个 `legacy-format3-*` 来源、先 `stock_data_list array_append` 后 `stock_data_list[N].raw_c set`；反向顺序、原生 `.cdmod`、其他父子路径和跨来源覆盖仍拒绝。
- 2026-08-17 实际安装两个 JSON 后的 BuildOnly 成功指纹为 `b3a8292d7976330df83b800f6a73ab512cf36fae65a9f1ceac99d1f23977c397`，新快照映射 24 个文件，统一 Format 3 日志为 9 个目标、2775 个生成 change、无 Format 3 skipped。这里的 change 是 writer 合并后的表/记录变更数，不等同于原始 5319 条 intent 数。
- 与旧成功快照逐字节比较：`nppgen` 原有 11 个 entry 全部不变，只新增 StoreInfo、DyeColorGroupInfo、NpcInfo 的 6 个 PABGB/PABGH entry；`nppv3_statusinfo`、`nppv3_stringinfo`、`nppv3_equipslotinfo`、`nppsa`、PATHC 和 5 个 standalone 均不变。最终 `nppv3_iteminfo` 精确等于旧合成 ItemInfo 再应用 2127 条价格 intent；四张最终表均与“旧有效 base + 对应新 intent”的内存结果完全相同。
- 冷构建仍保留此前已有的 `fat_stacks_plus_999999_except_weapons_1.18.1.json` 2/4970 条传统 byte patch 未匹配警告；这不是 Expanded Vendor 或本次 Format 3 writer 新增的跳过。BuildOnly 只证明产物和其他模组隔离，仍需用户启动游戏验证商店库存/价格、染色功能和存档加载。
- **真实原版全表验证门槛（强制）**：任何新 table parser、字段 walker 或数组定位器，
  必须先对当前游戏版本的真实 vanilla `.pabgb/.pabgh` 全表逐条验证：每条 entry
  能完整消费、PABGH offset 全部闭合、已知非空数组字段逐项回读；长度变化 writer
  还必须做完整表合成后的边界复核。合成 bytes 测试只能作为补充，不能替代真实原版
  全表验证。未通过该门槛不得进入 VFS BuildOnly，更不得进入实机。此次 NpcInfo
  旧尾部扫描算法虽然通过了合成测试，未经过真实 542 条原版全表验证就进入实机，
  造成了本轮长时间误判；今后禁止重复该流程。

## 游戏更新适配标准流程（每次游戏更新后必须照做）

游戏更新导致"模组不生效/跳过/崩溃"时，按以下顺序排障，禁止跳过或另起炉灶：

1. **先确认游戏版本**：读 `bin64\CrimsonDesert.exe` 的 FileVersion，对照 AGENTS.md/SKILL.md 已有基线（1.16.04=？、1.17=`2330`、1.18=`2443`）。确认该版本是否已适配，避免重复排查。
2. **先怀疑加载器，不怀疑模组**：把新模组 JSON 与更新前备份做 intent diff（key/field/new 全比较）。若 key 与字段一致、仅个别 new 不同，模组没变，失效根因在加载器 schema 漂移。Equip Everything V8→V9 就是这个模式。
3. **日志驱动定位**：看 `Format 3 bridge 处理 N 个模组、M 个目标，X 个生成补丁 / 跳过 Y 个 intent` 和跳过原因（`新增元素缺少 tag_name_hash`、`legacy fallback 未生效`、`未定位到 DMM V3 legacy prefab_data_list 尾部块` 等）。"被扫描到"不等于"已生效"，跳过数量就是断点。
4. **最小差分定位结构漂移，不要全量逆向**：
   - 用真实 intent 的 prefab hash / key 反查原版记录，定位字段真实位置（本次靠 hash 找到记录尾部 0x6A6）；
   - 用 writer 假设的结构解析真实字节，逐字段比较，找出字节差（长度变化、字段增删、计数宽度、尾部字节数）；
   - 不要花大量时间 dump 大片十六进制或尝试完整读懂记录结构；差分出"真实 vs 假设"即可动手。
5. **修复原则**：
   - parse/consume 兼容新旧布局：先按新版尝试，失败回退旧版（本次 7 字节 unk 尾部 → 3 字节回退）；
   - pack/serialize 输出跟随当前游戏版本，必要时以原版块实际形态决定（`_build_legacy_prefab_list_change` 检测原版尾部再打包）；
   - 只加目标字段的窄支持，禁止为单个模组重写整个 table writer。
6. **验证闭环**：
   - 新增回归测试覆盖新旧两种布局（本次 2 个新测试）；
   - 完整 `pytest test` 与 `ruff check .`；
   - 删 `.cdloader/vfs_state.json` 强制冷构建（保留 package cache 可加速），确认补丁数恢复（本次 299→2680）；
   - 解压新 `nppv3_*` 产物抽样验证目标记录字节（prefab hash 是否写入、PABGH key 数是否一致）；
   - 最后请用户实机确认游戏内效果。
7. **记录沉淀**：AGENTS.md 增加版本迁移基线章节 + SKILL.md 同步；记录适用版本、失效现象、根因（结构漂移点）、修复位置、回归测试、后续更新注意事项。

历次游戏更新结构漂移速查（按时间倒序）：

```text
1.18 (EXE 2443)  iteminfo: drop_default_data 与 prefab_data_list 间新增约 1256B；
                 legacy PrefabData 元素尾部 3B -> 7B（tribe 后新增 u32 unk=0xEAC5E173）
                 storeinfo: StockData 记录 1.17 在 +84..+87 插入 4B 0
                 （source = vanilla[:84] + 4B0 + vanilla[84:]），总长
                 1.17=123/136B、1.18=119/132B；转换删 +84..+87（不是 +100）；
                 legacy patch 多 entry 大幅变长会超 DYNAMIC_ENTRY_RELOCATION_WINDOW(512B)，
                 必须改用单个整表 change + `_pabgh_companion`
                 （2026-08-17 商店卖全部材料/装备/染料重放，见
                 references/all-craft-all-gear-all-dye-store-replay.md）
1.17 (EXE 2330)  iteminfo: 删除 inventory_info(u16)，equip_type_info 前移 2B；
                 buffinfo: ChangeBuffLevelBuffData 80->79、AddPercentInGameContentsBuffData 104->103（枚举前移）
1.16.04          iteminfo: item_tag_list 计数 u32 -> u16（equipable_hash 前错位 2B）；
                 Enemy HP Bar UI: class/scriptobject -> css/script；enemy health 标签 98 -> 97
1.12             iteminfo: 插入 _itemEffectInfo(u32)，位于 enable_equip_in_clone_actor 之后
1.09/1.10        iteminfo: 删除 extract_additional_drop_set_info；新增 unk_flag_109(u8)；
                 PrefabData equip_slot_list u16->u32、is_craft_material 位置前移
```

每次遇到新版本，先在速查表登记新行的根因与修复，保持这条链完整。禁止在未确认当前游戏版本的情况下直接套用旧 offset/schema。

## 2026-08-18 ItemInfo 语义计划混合批次整批跳过（DMM_AbyssGearUnlock 失效）

- 适用游戏版本：1.18.02（EXE 1.0.0.2474）。失效模组：`DMM_AbyssGearUnlock_v1.1.json`（190 条 `equipable_hash=0` 全不生效）。
- 现象日志：`cdmod语义计划-c9efad8cedb9: Format 3 目标 iteminfo.pabgb 跳过 2317 个 intent；2317x iteminfo 仅支持 ...`。2317 条 = DMM 190 条 + Expanded Vendor iteminfo 价格字段 2127 条。
- 根因判定：不是模组失效、不是游戏更新改表。1.18.02 原版表上 190/190 key 存在、`equipable_hash` 190/190 可定位、当前值非零（需要改成 0）。真因是 `cdmod_format3_bridge._bridge_family` 把单记录字段（`equipable_hash`）与价格字段（`price_list`/`buy_price_list`）混进同一个 `iteminfo-whole-fields` 家族，`build_iteminfo_prefab_result` 要求整批同类型才能路由，混合批次全部落到兜底路径 → 0 补丁。
- 隐患起始：2026-07-12 `b467a91` 引入统一语义合并时 `_bridge_family` 就对 iteminfo 无脑归 `iteminfo-whole-fields`；不是最近修改引入，8-06 `3fc0d2f` 反而是修复 1.16.04+ 布局的 `equipable_hash` 定位。Expanded Vendor 加入加载顺序后触发混批，把 DMM 一起带崩。
- 修复位置：`services/cdmod_format3_bridge.py` 新增 `iteminfo-price`（`is_iteminfo_price_field`）与 `iteminfo-record`（`ITEMINFO_RECORD_DIRECT_FIELDS`）两个家族并登记到 `_ordered_bridge_families`；`services/vfs_loader.py` `VFS_STATE_SCHEMA` 13→14 强制冷构建。
- 验证闭环：真实 1.18.02 表派发 `iteminfo-record` 190→190/0、`iteminfo-price` 2127→73/0，其余家族不变；新增回归测试 `test/test_cdmod_format3_bridge.py::test_bridge_splits_iteminfo_price_and_record_families`；`test/` 501 passed、`ruff check .` 通过。用户实机确认深渊装备可正常插槽。
- 注意事项：后续新增 iteminfo 字段类型必须先判断是否与现有 family 混批；价格/单记录/整表/窄字段必须拆批，禁止回退为单一 `iteminfo-whole-fields` 混批。
