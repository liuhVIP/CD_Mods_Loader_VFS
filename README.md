# cdloader 红色沙漠模组加载器

## 1. 项目介绍

`cdloader` 是《Crimson Desert（红色沙漠）》的独立模组加载器与 `.cdmod`
转换器。当前面向玩家推荐使用 `cdloader-VFS`：它在运行时将已合成的模组文件
虚拟映射给游戏，不会永久改写原版 PAZ/PAMT 归档。

### `.cdmod` 的优势

`.cdmod` 是统一的模组容器和能力声明格式，不是把旧模组简单改名为 ZIP。它保存稳定的
修改意图或必要资源，加载器则基于玩家当前游戏原版、最终加载顺序和活动语言生成实际文件。

- 单文件安装：JSON、loose files、资源与依赖信息集中在一个 `.cdmod`。
- 更适应更新：语义型修改按记录 key、字段与操作在最新原版上重建，不用把旧整表覆盖回新游戏。
- 多模组合成：loose、传统 JSON、Format 3 与资源替换先合成为最终结果，再映射给游戏。
- 小型分发：例如“拿取/偷窃显示价格”从 15,288,641 字节的完整中文 PALOC 缩小为
  1,576 字节的语义 `.cdmod`，只保存 24 条规则并保留玩家当前语言的原文。
- 可追踪：manifest、组件校验、构建统计和缓存让冲突、跳过与更新问题可以定位。

这不承诺任何旧模组永远适配未来游戏更新。遇到游戏结构变化或尚未支持的字段，加载器会
报告不兼容或跳过，而不会猜测写入。

### 已验证能力

下列项目已经完成容器转换、VFS 构建并在游戏内确认生效：

| 能力 | 代表验证 |
| --- | --- |
| Format 3 跨表与数组字段 | Equip Everything、No Fall Damage、4xAtkSpd |
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

## 2. 玩家：使用 cdloader-VFS 加载模组

### 准备

1. 从 [GitHub Releases](https://github.com/liuhVIP/cdmm/releases) 下载与模组匹配的
   `cdloader-VFS-v<版本>.zip`，并完整解压。
2. 将 ZIP 解压出的外层 `cdloader-VFS-v<版本>.exe` 和同级 `cdloader` 目录一起复制到游戏根目录。
   不能只复制 EXE。
3. 确认游戏根目录中存在 `bin64\CrimsonDesert.exe`，模组放在 `mods` 目录。

示例目录：

```text
G:\SteamLibrary\steamapps\common\Crimson Desert\
  bin64\CrimsonDesert.exe
  mods\
    SomeMod.cdmod
    SomeLegacyMod.json
    SomeLooseMod\
  cdloader-VFS-v<版本>.exe
  cdloader\
    cdloader-vfs-core.exe
```

### 启动

游戏完全退出后，双击游戏根目录的 `cdloader-VFS-v<版本>.exe`。加载器会自动扫描模组、
同步加载顺序、构建或复用 VFS 数据，然后启动游戏。普通使用不需要安装 Python，也不需要
手动编辑 PAZ、PAMT、PAPGT 或 PATHC。

Windows 每次重启后的首次 Steam 版启动会先进行一次纯 Steam 预热：进入游戏主菜单后正常退出，
加载器会自动继续 VFS 启动。后续同一开机周期的 VFS 启动不会重复预热。非 Steam 版本会跳过该步骤。

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

切换或删除模组后再次运行加载器即可；它会检测变化并重建。若提示未识别游戏目录，请确认
EXE 与 `cdloader` 目录都在 `bin64\CrimsonDesert.exe` 所在目录。游戏曾被 DMM 或其他工具
直接写入归档时，必须先恢复纯净原版文件，再使用 cdloader-VFS 构建。

## 3. 玩家与模组作者：使用 cdmod-converter 转换模组

转换器把当前支持的旧 JSON、loose files、DDS、standalone PAZ/PAMT、部分 Format 3 等内容
转换为 `.cdmod` 容器。它不会删除或改写源模组；转换完成后仍应使用同版本 `cdloader-VFS`
进行游戏内验证。

1. 从同一个 [GitHub Release](https://github.com/liuhVIP/cdmm/releases) 下载并完整解压
   `cdmod-converter-v<版本>.zip`。
2. 运行解压目录中的 `cdmod-converter-v<版本>.exe`。
3. 首次启动选择中文或 English。
4. 在控制台选择“转换单个模组”或“批量转换 mods 目录”，再按提示输入或拖入：游戏根目录、
   旧模组路径、`.cdmod` 输出目录。
5. 批量转换后检查输出目录中的 `conversion-report.json`，重点查看部分转换、跳过和失败项。

转换器会对可确认的格式生成最小必要数据；未知结构不会猜测写入。完整格式边界和作者发布建议请看
[`.cdmod 格式发布与使用教程`](docs/cdmod格式GitHub发布与使用教程.md)。转换或加载前同样必须确保
游戏是纯净原版，不能基于已经被其他管理器写入过的游戏归档继续转换。

## 4. 项目源码构建与开发

以下内容只面向需要修改源码、运行测试或自行构建 Release 的开发者。玩家应使用上面的
GitHub Release 成品，不需要执行这些命令。

### 开发环境

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

### 构建 Release

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
