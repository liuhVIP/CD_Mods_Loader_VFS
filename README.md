# cdloader 红色沙漠独立模组加载器

本项目是 Crimson Desert（红色沙漠）的独立命令行模组加载器。当前推荐使用 VFS 版本：

```text
T:\python_pro\cdmm\dist_nuitka\cdloader-VFS-v3.exe
```

`.cdmod` 玩家安装、模组作者转换、支持矩阵和兼容边界请参阅：

- [`.cdmod` 格式发布与使用教程](docs/cdmod格式GitHub发布与使用教程.md)

发布版本号统一由项目根目录的 `version.txt` 控制。VFS 打包脚本会据此生成成品文件名、控制台标题和 Windows PE 版本信息，发布时不需要再到源码中逐项修改版本号。

VFS 版本会先在游戏目录下生成虚拟加载包，再通过内置 VFS runtime 启动游戏。它不会直接改写游戏原始 PAZ/PAMT 归档，主要输出都放在游戏根目录的 `.cdloader` 目录中。

## 目录要求

游戏根目录必须能看到：

```text
bin64\CrimsonDesert.exe
```

模组需要放在游戏根目录下的 `mods` 文件夹中，例如：

```text
G:\SteamLibrary\steamapps\common\Crimson Desert
  bin64\CrimsonDesert.exe
  mods\
    SomeMod.json
    SomeLooseMod\
```

加载顺序优先使用：

```text
.cdloader\load_order.json
```

临时禁用模组请使用：

```text
.cdloader\disabled_mods.json
```

不要靠删除 `load_order.json` 里的条目来禁用模组，因为扫描时缺失条目会被自动补回。

## VFS 版本如何使用

最简单用法：

1. 复制 `dist_nuitka\cdloader-VFS-v3.exe` 到 Crimson Desert 游戏根目录。
2. 确认游戏已经完全退出，不要在游戏运行中重建 VFS。
3. 双击 `cdloader-VFS-v3.exe`。
4. 等待窗口显示 VFS 构建完成，并自动启动游戏。

放到游戏根目录后，程序会自动把自身所在目录识别为游戏目录。如果没有放在游戏根目录，会提示找不到：

```text
bin64\CrimsonDesert.exe
```

VFS 启动后会生成或复用这些文件：

```text
.cdloader\vfs_active\
.cdloader\vfs_mapping_tree.json
.cdloader\vfs_state.json
.cdloader\vfs_runtime\
.cdloader\logs\vfs_exe_launch.log
```

原生 VFS runtime 日志在：

```text
.cdloader\vfs_runtime\logs\vfs_launcher.log
.cdloader\vfs_runtime\logs\vfs_runtime.log
```

正常成功时，窗口会显示类似：

```text
VFS 构建完成：映射文件 ...\.cdloader\vfs_mapping_tree.json
VFS 输出目录：...\.cdloader\vfs_active
已映射文件数：N
正在通过 VFS 启动游戏...
模组加载完成，游戏已启动，窗口即将自动关闭。
```

## VFS 命令行用法

源码阶段可以用脚本构建并启动：

```powershell
Set-Location 'T:\python_pro\cdmm'
& '.\run_cdmm_vfs.bat'
```

指定游戏目录：

```powershell
& '.\run_cdmm_vfs.bat' -GameDir 'G:\SteamLibrary\steamapps\common\Crimson Desert'
```

只构建 VFS 包，不启动游戏：

```powershell
& '.\run_cdmm_vfs.bat' -GameDir 'G:\SteamLibrary\steamapps\common\Crimson Desert' -BuildOnly
```

打包后的 VFS exe 也支持命令行参数：

```powershell
& 'G:\SteamLibrary\steamapps\common\Crimson Desert\cdloader-VFS-v3.exe'
& 'T:\python_pro\cdmm\dist_nuitka\cdloader-VFS-v3.exe' --game-dir 'G:\SteamLibrary\steamapps\common\Crimson Desert'
& 'T:\python_pro\cdmm\dist_nuitka\cdloader-VFS-v3.exe' --game-dir 'G:\SteamLibrary\steamapps\common\Crimson Desert' --build-only
```

排障参数：

```powershell
# 严格要求所有 JSON/Format 3 目标都能在当前游戏 PAMT 中找到
--strict-targets

# 允许游戏进程仍在运行时继续执行，普通使用不建议
--allow-running-target

# 启用 NT OpenFile Hook，仅排障时使用
--enable-nt-open-file-hook

# 允许 VFS runtime patch ASI 模块，仅复现旧行为时使用
--patch-asi-modules
```

默认行为已经按红色沙漠当前验证结果处理：允许缺失目标、跳过 ASI 模块 IAT patch、不启用 NT OpenFile Hook。启动前会直接清理上一轮残留的 VFS 辅助进程和游戏目录下的 `crashpad_handler.exe`；如果清理失败，会提示用户手动结束对应 PID。

## VFS 加载原理

VFS 模式会按当前 `mods`、加载顺序、禁用列表和构建参数生成指纹。指纹一致且缓存完整时，会直接复用旧的 `.cdloader\vfs_active` 和 `.cdloader\vfs_mapping_tree.json`；只有模组、排序、参数或缓存产物变化时才冷构建。

构建流程大致是：

1. 扫描 `mods`，识别 JSON byte patch、Format 3、loose files、DDS、standalone PAZ/PAMT。
2. 按 `loose base -> JSON patch -> Format 3` 的顺序合成同一个目标文件，避免互相覆盖。
3. 按 DMM-like 方式拆分 VFS 包，例如 `nppv3_iteminfo`、`nppgen`、`nppsa`、`nppvoice` 等。
4. 生成 `.cdloader\vfs_active` 下的虚拟 PAZ/PAMT。
5. 生成 `.cdloader\vfs_mapping_tree.json`，让 VFS runtime 把游戏访问重定向到虚拟包。
6. 通过 `nppvfs_launcher.exe + vfs_runtime.dll` 启动 `bin64\CrimsonDesert.exe`。

已经验证的关键规则：

- VFS 包的 PAPGT flags 会统一规范化为 `003fff00`。
- VFS 只写 `.cdloader`，不直接污染游戏源文件。
- `.dds` 会进入 overlay，并配合 PATHC 映射。
- `.wem` 语音 loose 会拆入 `nppvoice`。
- `statusinfo` 包位目前是预留顺序，不能代表所有 statusinfo writer 都已完整实现。

## 如何打包最新 VFS exe

VFS 专用 Nuitka 打包脚本是：

```text
build_cdloader_vfs_nuitka.ps1
build_cdloader_vfs_nuitka.bat
```

推荐直接运行 `.bat` 包装脚本。脚本会优先使用 PowerShell 7，用户机器没有时会自动降级到系统自带 Windows PowerShell：

```powershell
Set-Location 'T:\python_pro\cdmm'
& '.\build_cdloader_vfs_nuitka.bat'
```

也可以直接调用 PowerShell 脚本：

```powershell
Set-Location 'T:\python_pro\cdmm'
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File '.\build_cdloader_vfs_nuitka.ps1'
```

默认输出：

```text
T:\python_pro\cdmm\dist_nuitka\cdloader-VFS-v3.exe
```

脚本会自动准备 `.venv-nuitka`，默认要求 Python `3.10.x`。如果本机没有可用环境，脚本会优先使用 `uv` 创建 Nuitka 构建环境并安装依赖。

打包前必须确认这两个 VFS runtime 文件存在：

```text
private\vfs_runtime\nppvfs_launcher.exe
private\vfs_runtime\vfs_runtime.dll
```

它们会被打进单体 exe，用户复制 `cdloader-VFS-v3.exe` 到游戏根目录后不再依赖本机源码目录。

VFS 原生启动器还依赖 VC/UCRT 运行库。打包脚本会从构建机的系统目录自动收集以下常见 DLL，随 `nppvfs_launcher.exe` 一起打进单体 exe 并释放到 `.cdloader\vfs_runtime\`：

```text
msvcp140.dll
vcruntime140.dll
vcruntime140_1.dll
ucrtbase.dll
```

这些 DLL 是已知常见缺失项，不代表完整依赖清单。如果构建机也缺少这些 DLL，脚本会给出警告；此时成品仍会要求用户系统已安装完整 Microsoft Visual C++ 2015-2022 x64 运行库。

用户侧优先从微软官方下载 x64 运行库，不建议单独下载 DLL：

```text
https://aka.ms/vc14/vc_redist.x64.exe
```

自定义输出名或输出目录：

```powershell
& '.\build_cdloader_vfs_nuitka.bat' -OutputName 'cdloader-VFS-v3' -DistDir 'dist_nuitka'
```

使用指定 Python：

```powershell
& '.\build_cdloader_vfs_nuitka.bat' -PythonPath 'T:\python_pro\cdmm\.venv-nuitka\Scripts\python.exe'
```

打包完成后会看到类似：

```text
VFS 专用 Nuitka 打包完成：T:\python_pro\cdmm\dist_nuitka\cdloader-VFS-v3.exe
复制 cdloader-VFS-v3.exe 到游戏根目录后双击，即默认构建 VFS 并启动游戏。
```

## 普通实体加载器

普通实体写入版本仍保留，产物是：

```text
dist\cdloader.exe
```

打包命令：

```powershell
Set-Location 'T:\python_pro\cdmm'
& '.\build_cdloader.bat'
```

运行方式：

```powershell
& '.\run_cdmm.bat'
& '.\dist\cdloader.exe' apply --game-dir 'G:\SteamLibrary\steamapps\common\Crimson Desert'
& '.\dist\cdloader.exe' scan --game-dir 'G:\SteamLibrary\steamapps\common\Crimson Desert'
```

普通版本无参数启动并确认位于游戏根目录后会显示菜单：

```text
1. 开始加载模组
2. 只扫描 mods，不写入游戏文件
3. 退出
```

普通实体加载日志在：

```text
.cdloader\logs\cold_load.log
.cdloader\logs\hot_load.log
.cdloader\logs\scan.log
```

## 开发环境

本目录下的 `.venv` 使用 `uv` 管理，解释器通常来自：

```text
E:\python\UV\uvpython\cpython-3.10.18-windows-x86_64-none\python.exe
```

初始化环境：

```powershell
Set-Location 'T:\python_pro'
uv venv cdmm\.venv --python E:\python\UV\uvpython\cpython-3.10.18-windows-x86_64-none\python.exe
uv pip install --python cdmm\.venv\Scripts\python.exe -r cdmm\requirements.txt
```

常用检查：

```powershell
Set-Location 'T:\python_pro\cdmm'
& '.\.venv\Scripts\python.exe' -m pytest
& '.\.venv\Scripts\python.exe' -m pytest tests\test_cdmm_loader.py
& '.\.venv\Scripts\python.exe' -m ruff check .
```

源码 CLI：

```powershell
Set-Location 'T:\python_pro'
& '.\cdmm\.venv\Scripts\python.exe' -m cdmm.cli apply --game-dir 'G:\SteamLibrary\steamapps\common\Crimson Desert'
& '.\cdmm\.venv\Scripts\python.exe' -m cdmm.cli scan --game-dir 'G:\SteamLibrary\steamapps\common\Crimson Desert'
& '.\cdmm\.venv\Scripts\python.exe' -m cdmm.cli revert --game-dir 'G:\SteamLibrary\steamapps\common\Crimson Desert'
```

源码开发阶段不传 `--game-dir` 时，才会读取：

```text
config\game_config.json
```

打包后的 exe 不读取这个开发配置。
