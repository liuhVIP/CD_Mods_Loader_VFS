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
后四个 DLL 是原生启动器常见的 VC/UCRT 运行库依赖，用于减少用户机器缺少运行库时
出现连环系统弹窗。它们不是完整依赖清单，用户机器仍建议安装完整 Microsoft Visual C++
2015-2022 x64 运行库。

发布给用户的 `nppvfs_launcher.exe` 和 `vfs_runtime.dll` 必须来自
`T:\C++\vfsDmoe\bin\x64\Release`。不要把 `bin\x64\Debug` 产物复制到这里，
否则用户会缺少 `MSVCP140D.dll`、`VCRUNTIME140D.dll`、`VCRUNTIME140_1D.dll`、
`ucrtbased.dll` 这类 Debug CRT；这些 DLL 不包含在普通 VC Redistributable 中。

打包脚本会把这些文件打进 `cdloader-VFS-v2.exe`，运行时释放到游戏目录的
`.cdloader/vfs_runtime/` 后再调用。
