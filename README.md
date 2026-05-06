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

统一启动脚本：

```powershell
cdmm\run_cdmm.bat
```

双击或运行后可以选择：

- 真实加载 mods 到游戏目录
- 只扫描 mods，不写入游戏文件
- 恢复加载器上次写入
- 开发自检（ruff + pytest，不加载真实游戏）

游戏根目录必须能看到 `meta\0.papgt`，并且模组需要放在该游戏目录下的
`mods` 文件夹中。

默认游戏根目录可在这里修改：

```text
cdmm\config\game_config.json
```

JSON 补丁如果出现少量 mismatch，独立加载器会继续半应用并输出警告。
