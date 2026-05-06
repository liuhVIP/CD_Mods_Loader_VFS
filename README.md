# cdloader 独立环境

本目录下的 `.venv` 使用 `uv` 管理，解释器来自：

```powershell
E:\python\UV\uvpython\cpython-3.10.18-windows-x86_64-none\python.exe
```

环境命令：

```powershell
uv venv cdmm\.venv --python E:\python\UV\uvpython\cpython-3.10.18-windows-x86_64-none\python.exe
uv pip install --python cdmm\.venv\Scripts\python.exe -r cdmm\requirements.txt

$env:PYTHONPATH = "src"
& cdmm\.venv\Scripts\python.exe -m pytest tests\test_cdmm_loader.py -q
& cdmm\.venv\Scripts\python.exe -m ruff check cdmm tests\test_cdmm_loader.py
& cdmm\.venv\Scripts\python.exe -m cdmm.cli --help
```

## 控制台运行模式

源码环境：

```powershell
Set-Location T:\python_pro
& .\cdmm\.venv\Scripts\python.exe -m cdmm.cli apply --game-dir "G:\SteamLibrary\steamapps\common\Crimson Desert"
& .\cdmm\.venv\Scripts\python.exe -m cdmm.cli scan --game-dir "G:\SteamLibrary\steamapps\common\Crimson Desert"
```

单体 exe 打包后：

```powershell
.\dist\cdloader.exe
.\dist\cdloader.exe apply --game-dir "G:\SteamLibrary\steamapps\common\Crimson Desert"
.\dist\cdloader.exe scan --game-dir "G:\SteamLibrary\steamapps\common\Crimson Desert"
```

推荐把 `cdloader.exe` 直接放到 Crimson Desert 游戏根目录下，双击后会自动使用程序所在目录作为游戏根目录。打包后的 exe 不会读取开发用的 `config\game_config.json`；如果程序所在目录不是游戏根目录，会提示把 exe 放到游戏根目录。带参数调用时 `--game-dir` 优先级最高，会直接执行并返回退出码，方便其他 Python 客户端用 `subprocess` 接入。

当前主菜单：

- `1` 开始加载模组。
- `2` 只扫描 mods，不写入游戏文件。
- `3` 退出。

执行完成后会回到菜单。真实加载时控制台只显示 tqdm 进度条和最终结果，详细日志统一写入游戏目录下的：

```text
.cdloader\logs\cold_load.log
.cdloader\logs\hot_load.log
```

首次加载写入 `cold_load.log`，后续加载写入 `hot_load.log`，同类新日志会覆盖旧日志。

打包命令：

```powershell
cdmm\build_cdloader.bat
```

打包产物：

```text
cdmm\dist\cdloader.exe
```

统一启动脚本：

```powershell
cdmm\run_cdmm.bat
```

双击或运行后可以选择：

- 开始加载模组
- 只扫描 mods，不写入游戏文件
- 退出

游戏根目录必须能看到 `bin64\CrimsonDesert.exe`，并且模组需要放在该游戏目录下的
`mods` 文件夹中。

不传 `--game-dir` 时，加载器按下面顺序解析游戏根目录：

1. 打包后的 exe：程序所在目录存在 `bin64\CrimsonDesert.exe`，直接使用程序所在目录；否则提示把 exe 放到游戏根目录。
2. 源码开发阶段：如果当前程序目录不是游戏根目录，再读取 `config\game_config.json`。
3. 源码开发阶段：配置不存在时让用户手动输入。

开发阶段默认配置可在这里修改：

```text
cdmm\config\game_config.json
```

JSON 补丁如果出现少量 mismatch，独立加载器会继续半应用并输出警告。
