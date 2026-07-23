# VFS Runtime 二进制目录

本目录用于放置虚拟文件加载需要的关键运行组件：

- `nppvfs_launcher.exe`
- `vfs_runtime.dll`
- `msvcp140.dll`
- `vcruntime140.dll`
- `vcruntime140_1.dll`
- `ucrtbase.dll`

`nppvfs_launcher.exe` 负责启动目标游戏并注入 VFS runtime，`vfs_runtime.dll`
负责在游戏进程内按 `.cdloader/vfs_mapping_tree.json` 执行虚拟文件重定向。
源码工程生成的 `vfs_launcher.exe` 同步到本目录时必须重命名为
`nppvfs_launcher.exe`，以匹配 Python 启动器与发布目录的固定资产名称。
后四个 DLL 是原生启动器常见的 VC/UCRT 运行库依赖，用于减少用户机器缺少运行库时
出现连环系统弹窗。它们不是完整依赖清单，用户机器仍建议安装完整 Microsoft Visual C++
2015-2022 x64 运行库。

发布给用户的 `nppvfs_launcher.exe` 和 `vfs_runtime.dll` 必须来自
`T:\C++\vfsDmoe\bin\x64\Release`。不要把 `bin\x64\Debug` 产物复制到这里，
否则用户会缺少 `MSVCP140D.dll`、`VCRUNTIME140D.dll`、`VCRUNTIME140_1D.dll`、
`ucrtbased.dll` 这类 Debug CRT；这些 DLL 不包含在普通 VC Redistributable 中。

2026-07-23 当前发布基线：

```text
nppvfs_launcher.exe SHA-256: 44C1B617C12D1C5C694408EB22EA132E9F5D25843678EF88C762C8CC7A5AFFD2
vfs_runtime.dll SHA-256: C20C4791B10AAB6E983BD74A87E3409A7984102D6FDD01042B5FBDDED3853436
```

当前 launcher 支持由上层通过 `--log-dir` 显式指定 native 日志目录；正式加载器统一
写入游戏目录下的 `.cdloader/vfs_runtime/logs/`，不再依赖开发目录层级推导。

该 runtime 使用 `SafetyHook=7 + stable system NT IAT=14` 窄混合后端，SafetyHook、
Zydis 与 Zycore 已静态链接，不需要额外发布对应 DLL。打包脚本会把本目录文件打进
当前 `cdloader-VFS-v<版本>` 目录版，运行时释放到游戏目录的
`.cdloader/vfs_runtime/` 后再调用。
