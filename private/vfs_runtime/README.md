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

2026-07-16 当前实机稳定基线：

```text
nppvfs_launcher.exe SHA-256: 5FB8C58286C1A70E98A5496C9F93B6AD626AEA0981178BF98D3C7C22019CB33B
vfs_runtime.dll SHA-256: 9337BA64F1465AB06D2C86949AD4AE598E41DE9A6FA563B6B16A73DE65F2F3EF
```

该 runtime 使用 `SafetyHook=7 + stable system NT IAT=14` 窄混合后端，SafetyHook、
Zydis 与 Zycore 已静态链接，不需要额外发布对应 DLL。打包脚本会把本目录文件打进
当前 `cdloader-VFS-v<版本>` 目录版，运行时释放到游戏目录的
`.cdloader/vfs_runtime/` 后再调用。
