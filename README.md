# cdloader Crimson Desert Mod Loader / 红色沙漠模组加载器

[English](#english) | [中文](#中文)

<a id="english"></a>

## English

### 1. Overview

`cdloader` is a standalone mod loader and `.cdmod` converter for Crimson Desert. For players,
the recommended edition is `cdloader-VFS`: the loader first analyzes and composes mods according
to the final load order, then its runtime VFS maps the generated results to the paths the game
normally reads. The game sees the merged modded files, while the original PAZ/PAMT archives on
disk are not permanently rewritten.

This approach is similar in concept to Mod Organizer 2 (MO2) virtual loading: mods remain in a
separate location and participate through a virtual file view only while the game is running.
Switching, disabling, or reordering mods therefore does not require repeatedly overwriting the
original game files. `cdloader-VFS` is not an MO2 plugin and does not reuse MO2's general-purpose
virtual file system implementation. It is a dedicated loading layer for Crimson Desert's
PAZ/PAMT/PAPGT/PATHC structures, table composition, and ASI startup chain.

#### Why `.cdmod`

`.cdmod` is a unified mod container and capability declaration format, not an old mod renamed to
a ZIP file. It stores stable modification intent or required resources, while the loader generates
the actual game-facing files from the player's current vanilla data, final load order, and active
language.

- Single-file installation: table rules, loose files, resources, metadata, and dependencies can
  be stored in one `.cdmod`.
- Better update resilience: semantic changes are rebuilt on the latest vanilla data using record
  keys, dynamic selectors, fields, and operations instead of restoring an outdated complete table.
- Multi-mod composition: when several mods change different records or fields in the same table,
  the loader can apply them to one current vanilla base and generate a single final table. If two
  mods change the same field, final load order determines which value wins.
- Unified capabilities: one container can declare semantic table changes, PALOC localization,
  resource replacements and transforms, legacy byte patches, and standalone PAZ/PAMT archives
  when they are genuinely required.
- Smaller distribution: for example, Display Take and Steal Price was reduced from a complete
  15,288,641-byte Chinese PALOC file to a 1,576-byte semantic `.cdmod` containing only 24 rules
  while preserving the player's current-language text.
- Validation and diagnostics: manifests, component SHA-256 hashes, dependencies, conflict
  analysis, build statistics, and caches make corruption, overwrites, skipped operations, and
  update problems easier to trace.

A typical semantic workflow is:

```text
Author's Format 3 / JSON v3.1 modification intent
  -> cdmod-converter normalizes selector + field + operation + value
  -> .cdmod stores rules without a complete table that can be rebuilt from the current game
  -> cdloader reads the player's current vanilla table
  -> compatible changes are merged in load_order and PABGB/PABGH are rebuilt
  -> VFS maps the single final result to the game at runtime
```

##### JSON v3.1 versus `.cdmod`

JSON v3.1 is a **semantic language for table modifications**. `.cdmod` is a **complete container
and runtime contract** that can carry those semantic operations together with other mod resources.
They are not simple replacements for one another, and conversion cannot invent information that
does not exist in the source mod.

| Comparison | JSON v3.1 | `.cdmod` |
| --- | --- | --- |
| Primary purpose | Describe target tables, record selection, fields, and values | Package semantic patches, resources, metadata, dependencies, and integrity data |
| Complete resources | Primarily intended for table operations | Can include required DDS, WEM, PAA, PAC, models, and other resources |
| Multi-mod handling | Depends on the tool that loads it | Uses cdloader's unified ordering, conflict analysis, composition, and VFS pipeline |
| Game updates | Decoder/schema adaptation may still be required when a target field changes | A writer update may still be required, but adaptation is centralized in the loader and existing packages usually do not need individual rebuilds |
| Distribution and validation | Usually a standalone JSON file | Single-file manifest, component SHA-256 hashes, version, and dependency declarations |

For pure semantic table changes, `.cdmod` cannot magically eliminate every schema change. If the
game genuinely changes the binary structure of a target field, the corresponding writer still
needs to be updated. The advantage is that this adaptation is centralized in the loader. After
the writer is updated, existing `.cdmod` packages that preserve the original intent can normally
be rebuilt without every author redistributing a complete table.

Semantic patches must also be distinguished from complete archives. The current converter can
normalize supported Format 3 / JSON v3.1 operations into field-level rules. When an old mod only
provides standalone `0.paz + 0.pamt`, however, the converter preserves and validates that archive
as-is. It does not automatically infer field differences from complete PABGB/PABGH tables or
silently remove those tables. Table mod authors should therefore prefer Format 3 / JSON v3.1 or
native `.cdmod` semantic operations. Textures, models, animations, audio, and similar resources
should still include the complete files they require.

This is not a promise that every old mod will work forever after every game update. When the game
structure changes or a field is not yet supported, the loader reports or skips the incompatible
operation instead of guessing and writing unsafe data.

#### Verified capabilities

The following representative capabilities have completed container conversion, VFS builds, and
in-game verification:

| Capability | Representative verification |
| --- | --- |
| Format 3 cross-table and array fields | Equip Everything, No Fall Damage, Direct Attack Speed 4x and Direct Movement Speed 4x (always active without stat gear) |
| Multilingual PALOC | Display Take and Steal Price preserves the active language and appends prices |
| Resource transforms and replacements | K-Makeup for Cordelia, Male Glide Animation, Electro Mecha Longsword To Lightsaber |
| Legacy and resource mods | legacy JSON, loose/DDS/WEM/PAA/PAC, standalone PAZ/PAMT |

Support is implemented by target table, field, and resource structure rather than hard-coded mod
names. See the [`.cdmod` format release and usage guide](docs/cdmod格式GitHub发布与使用教程.md)
for the complete support matrix, limitations, and author guidance.

The project publishes two version-matched release packages:

```text
cdloader-VFS-v<version>.zip       Loads mods for players
cdmod-converter-v<version>.zip   Converts existing mods to .cdmod
```

Always download the loader and converter from the same GitHub tag/release. The project root
[`version.txt`](version.txt) is the only release-version source; source code, artifact names, and
Windows file versions are all derived from it.

### 2. Players: loading mods with cdloader-VFS

#### Preparation

1. Download `cdloader-VFS-v<version>.zip` from
   [GitHub Releases](https://github.com/liuhVIP/cdmm/releases) and fully extract it.
2. Copy both the outer `cdloader-VFS-v<version>.exe` and the adjacent `cdloader` directory into
   the game root. Do not copy the EXE by itself.
3. Confirm that the game root contains `bin64\CrimsonDesert.exe`, then place mods in `mods`.

Example layout:

```text
G:\SteamLibrary\steamapps\common\Crimson Desert
  mods\
    SomeMod.cdmod
    SomeLegacyMod.json
    SomeLooseMod\
  cdloader-VFS-v<version>.exe
  cdloader\
    cdloader-vfs-core.exe
```

Conversion is optional. cdloader-VFS can directly load recognized legacy JSON byte patches,
supported Format 3 / JSON v3.1 operations, loose files, DDS and other supported resources, and
standalone PAZ/PAMT archives. Convert them to `.cdmod` when you want single-file packaging,
validation, standardized metadata, and the unified container workflow.

#### Launching

After the game has fully exited, run `cdloader-VFS-v<version>.exe` from the game root. The loader
automatically scans mods, synchronizes load order, builds or reuses VFS data, and launches the
game. Normal use does not require Python or manual PAZ, PAMT, PAPGT, or PATHC editing.

On the first Steam launch after each Windows restart, the loader performs one clean Steam warm-up.
Enter the main menu and exit normally; the loader then continues automatically with the VFS launch.
Later VFS launches in the same Windows boot session normally reuse that warm-up. If a later VFS
launch stops at data load `2/12` and Windows reports `0xC0000005` at
`CrimsonDesert.exe+0xAD164D0`, the earlier warm-up is invalidated and the loader automatically runs
a clean recovery warm-up before trying VFS again. Non-Steam editions skip this step.

Each cold build now writes to a unique immutable directory under `.cdloader\vfs_active` and only
switches the mapping after the snapshot is complete. Removing a crashing mod therefore does not
reuse the same absolute PAZ/PAMT source path that was active during the failed launch.

VFS build data and logs are stored under `.cdloader` in the game root:

```text
.cdloader\load_order.json          Mod load order; lower entries have higher priority
.cdloader\disabled_mods.json       Temporarily disabled mods
.cdloader\logs\vfs_exe_launch.log Launch log
.cdloader\vfs_runtime\logs\       Native VFS runtime logs
```

Do not disable a mod by deleting it from `load_order.json`; missing entries are added back during
scanning. Use `disabled_mods.json`. If another manager is used, let it download, install, or organize
mods only. Do not let another manager mount mods or launch the game while using cdloader-VFS.

When no explicit order exists, mods are ordered by modification time from oldest to newest. Newer
mods load later and therefore have higher overwrite priority; equal timestamps use the path name as
a stable tiebreaker.

Run the loader again after changing or removing mods; it detects the change and rebuilds as needed.
If the game directory is not recognized, confirm that both the EXE and `cdloader` directory are in
the directory containing `bin64\CrimsonDesert.exe`. If DMM or another tool has directly modified
the game archives, restore clean vanilla files before building with cdloader-VFS.

### 3. Players and authors: converting mods with cdmod-converter

The converter packages supported legacy JSON, loose files, DDS, standalone PAZ/PAMT, and the
currently implemented subset of Format 3 / JSON v3.1 writer capabilities into `.cdmod`. It does
not delete or modify source mods. Converted packages should still be verified in game with the
matching cdloader-VFS release.

1. Download and fully extract `cdmod-converter-v<version>.zip` from the same
   [GitHub Release](https://github.com/liuhVIP/cdmm/releases).
2. Run `cdmod-converter-v<version>.exe` from the extracted directory.
3. Select Chinese or English on first launch.
4. Choose **Convert one mod** or **Convert a mods directory**, then enter or drag in the requested
   game root, source mod or `mods` directory, and `.cdmod` output directory.
5. **Convert a mods directory** performs one batch conversion of the entire supported mod set.
   Review `conversion-report.json` afterward for partial conversions, skipped content, and failures.

The converter chooses behavior by source format. Confirmed semantic JSON and PALOC structures keep
only the required rules; textures, models, animations, and audio keep their required complete
resources; standalone PAZ/PAMT retains the archive contents and is not promised to become a
field-level semantic difference automatically. Unknown structures are not guessed.

See the [`.cdmod` format release and usage guide](docs/cdmod格式GitHub发布与使用教程.md) for
complete format boundaries and publishing guidance. Before converting or loading, ensure that the
game is clean vanilla. Do not convert against PAZ/PAMT/PABGB/PALOC files previously modified by
another manager.

### 4. Source builds and development

This section is only for contributors who need to modify source code, run tests, or build releases.
Players should use the GitHub Release artifacts above and do not need these commands.

#### Development environment

Use the repository `.venv` and PowerShell 7:

```powershell
Set-Location 'T:\python_pro\cdmm'
& '.\.venv\Scripts\python.exe' -m pytest
& '.\.venv\Scripts\python.exe' -m ruff check .
```

Build VFS data and launch from source:

```powershell
Set-Location 'T:\python_pro\cdmm'
& '.\run_cdmm_vfs.bat' -GameDir 'G:\SteamLibrary\steamapps\common\Crimson Desert'
```

Build VFS data without launching the game:

```powershell
& '.\run_cdmm_vfs.bat' -GameDir 'G:\SteamLibrary\steamapps\common\Crimson Desert' -BuildOnly
```

#### Building releases

Build the VFS loader:

```powershell
Set-Location 'T:\python_pro\cdmm'
& '.\build_cdloader_vfs_nuitka.bat'
```

Build the `.cdmod` converter:

```powershell
Set-Location 'T:\python_pro\cdmm'
& '.\build_cdmod_converter.bat'
```

Build scripts read [`version.txt`](version.txt) and generate version-matched VFS and converter ZIPs
under `dist_nuitka`. Official VFS releases must use the directory layout where the outer EXE and
the `cdloader` directory are adjacent. Do not revert the VFS core to Nuitka `--onefile` mode.

The traditional physical-write loader remains available for development compatibility scenarios:

```powershell
& '.\build_cdloader.bat'
& '.\dist\cdloader.exe' apply --game-dir 'G:\SteamLibrary\steamapps\common\Crimson Desert'
```

Its development configuration reads `config\game_config.json` only when no `--game-dir` is supplied
during source execution. Packaged executables do not read that configuration.

<a id="中文"></a>

## 中文

### 1. 项目介绍

`cdloader` 是《Crimson Desert（红色沙漠）》的独立模组加载器与 `.cdmod`
转换器。当前面向玩家推荐使用 `cdloader-VFS`：加载器先按最终排序分析并合成模组，
再由运行时 VFS 将生成结果虚拟映射到游戏原本要读取的路径。游戏看到的是已经合并的
模组文件，磁盘上的原版 PAZ/PAMT 归档不会被永久改写。

这种使用理念类似 Mod Organizer 2（MO2）的虚拟加载：模组保留在独立位置，只在游戏运行时
以虚拟文件视图参与读取，因此切换、禁用或调整顺序后不需要反复覆盖原版文件。`cdloader-VFS`
不是 MO2 插件，也没有照搬 MO2 的通用虚拟文件系统实现；它是针对 Crimson Desert 的
PAZ/PAMT/PAPGT/PATHC 结构、表格合成和 ASI 启动链设计的专用加载层。

#### `.cdmod` 的优势

`.cdmod` 是统一的模组容器和能力声明格式，不是把旧模组简单改名为 ZIP。它保存稳定的
修改意图或必要资源，加载器则基于玩家当前游戏原版、最终加载顺序和活动语言生成实际文件。

- 单文件安装：表格规则、loose files、资源、元数据与依赖信息集中在一个 `.cdmod`。
- 更适应更新：语义型修改按记录 key、动态选择条件、字段与操作在最新原版上重建，不用把
  旧整表覆盖回新游戏。
- 多模组合成：多个模组修改同一张表但目标记录或字段不同时，加载器可以在同一份最新原表上
  依次应用修改并只生成一份最终表；修改同一字段时再由最终加载顺序决定覆盖关系。
- 统一能力：同一容器可以声明表格语义修改、PALOC 本地化、资源替换与变换、传统 byte patch，
  以及确有必要的 standalone PAZ/PAMT，不再让多种安装结构散落在模组目录中。
- 小型分发：例如“拿取/偷窃显示价格”从 15,288,641 字节的完整中文 PALOC 缩小为
  1,576 字节的语义 `.cdmod`，只保存 24 条规则并保留玩家当前语言的原文。
- 可校验、可追踪：manifest、组件 SHA-256、依赖、冲突分析、构建统计和缓存让损坏、覆盖、
  跳过与更新问题可以定位。

典型的语义型处理链如下：

```text
作者的 Format 3 / JSON v3.1 修改意图
  -> cdmod-converter 规范化为 selector + field + operation + value
  -> .cdmod 保存规则，不携带可从当前游戏重建的完整表
  -> cdloader 读取玩家当前版本的原版表
  -> 按 load_order 合并所有兼容修改并重建 PABGB/PABGH
  -> VFS 在运行时把唯一的最终结果映射给游戏
```

##### 与 JSON v3.1 的关系

JSON v3.1 是表格修改的**语义描述格式**；`.cdmod` 是可以承载这种语义描述及其他模组资源的
**完整容器与运行规范**。两者不是简单的替代关系，转换过程也不会凭空创造源模组没有的信息。

| 对比项 | JSON v3.1 | `.cdmod` |
| --- | --- | --- |
| 主要职责 | 描述目标表、记录选择、字段与新值 | 封装语义补丁、资源、元数据、依赖和校验信息 |
| 完整资源 | 主要面向表格意图 | 可同时携带 DDS、WEM、PAA、PAC、模型等必要完整资源 |
| 多模组处理 | 取决于实际加载它的工具 | 统一进入 cdloader 的排序、冲突分析、合成和 VFS 链路 |
| 游戏更新 | 目标字段结构改变时仍可能需要 decoder/schema 适配 | 对应 writer 仍可能需要更新，但更新集中在加载器；已有包通常无需逐个重做 |
| 分发与校验 | 通常是单独 JSON | 单文件 manifest、组件 SHA-256、版本与依赖声明 |

对纯表格语义修改而言，`.cdmod` 不会神奇地消除所有 schema 变化：如果游戏真正改变了目标字段
的二进制结构，对应 writer 仍需更新。它的优势是把这项适配集中到加载器；writer 更新后，保存
原始修改意图的已有 `.cdmod` 通常可以直接重新构建，而不需要每位作者重新发布完整表。

还需要区分语义补丁和完整归档：当前转换器能把 Format 3 / JSON v3.1 规范化为字段级规则，
但面对只提供 standalone `0.paz + 0.pamt` 的旧模组时，会原样封装并校验该归档，不会自动从
其中的完整 PABGB/PABGH 表反推出字段差异，也不会擅自删除完整表。因此，表格模组作者应优先
发布 Format 3 / JSON v3.1 或原生 `.cdmod` 语义操作；贴图、模型、动画、音频等资源则仍应携带
必要的完整文件。

这不承诺任何旧模组永远适配未来游戏更新。遇到游戏结构变化或尚未支持的字段，加载器会
报告不兼容或跳过，而不会猜测写入。

#### 已验证能力

下列项目已经完成容器转换、VFS 构建并在游戏内确认生效：

| 能力 | 代表验证 |
| --- | --- |
| Format 3 跨表与数组字段 | Equip Everything、No Fall Damage、Direct Attack Speed 4x与Direct Movement Speed 4x（无需对应属性装备直接生效） |
| 多语言 PALOC | Display Take and Steal Price 按活动语言保留原文并追加价格 |
| 资源变换与替换 | K-Makeup for Cordelia、Male Glide Animation、Electro Mecha Longsword To Lightsaber |
| 传统与资源型模组 | legacy JSON、loose/DDS/WEM/PAA/PAC、standalone PAZ/PAMT |

支持按目标表、字段和资源结构实现，不按某个模组名称硬编码。完整支持矩阵、限制与作者指南见
[`.cdmod 格式发布与使用教程`](docs/cdmod格式GitHub发布与使用教程.md)。

项目同时提供两类版本一致的 Release 成品：

```text
cdloader-VFS-v<版本>.zip       玩家加载模组
cdmod-converter-v<版本>.zip   转换旧模组为 .cdmod
```

请始终从同一个 GitHub Tag / Release 下载加载器和转换器。发布版本唯一由项目根目录
[`version.txt`](version.txt) 控制；当前源码、成品名和 Windows 文件版本都从这里派生。

### 2. 玩家：使用 cdloader-VFS 加载模组

#### 准备

1. 从 [GitHub Releases](https://github.com/liuhVIP/cdmm/releases) 下载与模组匹配的
   `cdloader-VFS-v<版本>.zip`，并完整解压。
2. 将 ZIP 解压出的外层 `cdloader-VFS-v<版本>.exe` 和同级 `cdloader` 目录一起复制到游戏根目录。G:\SteamLibrary\steamapps\common\Crimson Desert
   不能只复制 EXE。
3. 确认游戏根目录中存在 `bin64\CrimsonDesert.exe`，模组放在 `mods` 目录。

示例目录：

```text
G:\SteamLibrary\steamapps\common\Crimson Desert
  mods\
    SomeMod.cdmod
    SomeLegacyMod.json
    SomeLooseMod\
  cdloader-VFS-v<版本>.exe
  cdloader\
    cdloader-vfs-core.exe
```

转换不是必需步骤。cdloader-VFS 可以直接加载已识别的传统 JSON byte patch、已支持的
Format 3 / JSON v3.1 操作、loose files、DDS 与其他已支持资源，以及 standalone PAZ/PAMT。
当你需要单文件封装、校验、标准化元数据和统一容器工作流时，再转换为 `.cdmod`。

#### 启动

游戏完全退出后，双击游戏根目录的 `cdloader-VFS-v<版本>.exe`。加载器会自动扫描模组、
同步加载顺序、构建或复用 VFS 数据，然后启动游戏。普通使用不需要安装 Python，也不需要
手动编辑 PAZ、PAMT、PAPGT 或 PATHC。

Windows 每次重启后的首次 Steam 版启动会先进行一次纯 Steam 预热：进入游戏主菜单后正常退出，
加载器会自动继续 VFS 启动。后续同一开机周期通常复用该预热；如果之后某次 VFS 启动停在数据加载
`2/12`，且 Windows 报告 `0xC0000005 / CrimsonDesert.exe+0xAD164D0`，加载器会立即作废旧预热，
并在下一次 VFS 启动前自动执行恢复性纯 Steam 预热。非 Steam 版本会跳过该步骤。

每次冷构建会在 `.cdloader\vfs_active` 下创建唯一、不可变的活动快照，全部文件完成后才切换
映射。这样删除导致崩溃的模组后，不会继续复用失败启动时相同的 PAZ/PAMT 绝对源路径。

VFS 构建数据和日志保存在游戏根目录的 `.cdloader`：

```text
.cdloader\load_order.json          模组加载顺序，越靠下优先级越高
.cdloader\disabled_mods.json       临时禁用的模组
.cdloader\logs\vfs_exe_launch.log 启动日志
.cdloader\vfs_runtime\logs\       原生 VFS runtime 日志
```

不要通过删除 `load_order.json` 中的条目禁用模组，扫描时缺失项会自动补回；请使用
`disabled_mods.json`。如果使用其他管理器，只让它负责下载、安装与排序，不能让它挂载
模组或启动游戏。

未提供显式排序时，模组默认按修改时间从旧到新加载；修改时间越新越靠后、覆盖优先级越高，
时间相同则按路径名称稳定排序。

切换或删除模组后再次运行加载器即可；它会检测变化并重建。若提示未识别游戏目录，请确认
EXE 与 `cdloader` 目录都在 `bin64\CrimsonDesert.exe` 所在目录。游戏曾被 DMM 或其他工具
直接写入归档时，必须先恢复纯净原版文件，再使用 cdloader-VFS 构建。

### 3. 玩家与模组作者：使用 cdmod-converter 转换模组

转换器把当前支持的旧 JSON、loose files、DDS、standalone PAZ/PAMT，以及已实现 writer / capability
覆盖的部分 Format 3 / JSON v3.1 内容转换为 `.cdmod` 容器。它不会删除或改写源模组；转换完成后
仍应使用同版本 `cdloader-VFS` 进行游戏内验证。

1. 从同一个 [GitHub Release](https://github.com/liuhVIP/cdmm/releases) 下载并完整解压
   `cdmod-converter-v<版本>.zip`。
2. 运行解压目录中的 `cdmod-converter-v<版本>.exe`。
3. 首次启动选择中文或 English。
4. 在控制台选择“转换单个模组”或“批量转换 mods 目录”，再按提示输入或拖入：游戏根目录、
   旧模组路径、`.cdmod` 输出目录。
5. “批量转换 mods 目录”会一次扫描并转换整个目录中的受支持模组。完成后检查输出目录中的
   `conversion-report.json`，重点查看部分转换、跳过和失败项。

转换器会按源格式选择处理方式：语义 JSON、PALOC 等可确认结构只保存必要规则；贴图、模型、
动画和音频保存必要完整资源；standalone PAZ/PAMT 则保持归档内容，不承诺自动转成字段级差异。
未知结构不会猜测写入。完整格式边界和作者发布建议请看
[`.cdmod 格式发布与使用教程`](docs/cdmod格式GitHub发布与使用教程.md)。转换或加载前同样必须确保
游戏是纯净原版，不能基于已经被其他管理器写入过的游戏归档继续转换。

### 4. 项目源码构建与开发

以下内容只面向需要修改源码、运行测试或自行构建 Release 的开发者。玩家应使用上面的
GitHub Release 成品，不需要执行这些命令。

#### 开发环境

项目优先使用本仓库的 `.venv` 与 PowerShell 7：

```powershell
Set-Location 'T:\python_pro\cdmm'
& '.\.venv\Scripts\python.exe' -m pytest
& '.\.venv\Scripts\python.exe' -m ruff check .
```

源码阶段的 VFS 构建与启动：

```powershell
Set-Location 'T:\python_pro\cdmm'
& '.\run_cdmm_vfs.bat' -GameDir 'G:\SteamLibrary\steamapps\common\Crimson Desert'
```

只构建 VFS 数据、不启动游戏：

```powershell
& '.\run_cdmm_vfs.bat' -GameDir 'G:\SteamLibrary\steamapps\common\Crimson Desert' -BuildOnly
```

#### 构建 Release

构建 VFS 加载器：

```powershell
Set-Location 'T:\python_pro\cdmm'
& '.\build_cdloader_vfs_nuitka.bat'
```

构建 `.cdmod` 转换器：

```powershell
Set-Location 'T:\python_pro\cdmm'
& '.\build_cdmod_converter.bat'
```

构建脚本读取 [`version.txt`](version.txt)，在 `dist_nuitka` 生成版本一致的 VFS 发布 ZIP
与转换器 ZIP。VFS 正式发布必须使用目录版结构：外层 EXE 与 `cdloader` 目录同级；不要将
VFS core 回退为 Nuitka `--onefile` 单文件模式。

传统实体写入加载器仍保留给开发兼容性场景：

```powershell
& '.\build_cdloader.bat'
& '.\dist\cdloader.exe' apply --game-dir 'G:\SteamLibrary\steamapps\common\Crimson Desert'
```

其开发配置仅在源码阶段未提供 `--game-dir` 时读取 `config\game_config.json`；打包后的成品
不会读取该配置。

.\run_cdmm_vfs.bat -AllowMissingTargets -NoBuildVfsDemo -KeepRunning

