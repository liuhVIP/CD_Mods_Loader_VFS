# VFS Runtime 二进制目录

本目录用于放置虚拟文件加载需要的关键运行组件：

- `nppvfs_launcher.exe`
- `vfs_runtime.dll`

`nppvfs_launcher.exe` 负责启动目标游戏并注入 VFS runtime，`vfs_runtime.dll`
负责在游戏进程内按 `.cdloader/vfs_mapping_tree.json` 执行虚拟文件重定向。
打包脚本会把这两个文件打进 `cdloader-VFS-v1.exe`，运行时释放到游戏目录的
`.cdloader/vfs_runtime/` 后再调用。
