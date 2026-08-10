# Trinity 0.13.2 运行时汉化

本工程生成独立的 `TrinityCN.asi`，不修改原始 `Trinity.asi`。英文到中文的映射只在开发阶段保存在
`translations.zh-CN.json`，构建时转换为 C++ 只读数据并编译进 ASI；发布目录禁止携带 JSON。
游戏内右上角版本号前、默认说明栏和 Windows 文件属性均带有 `b站up 改名_汉化` 标注。

物品添加、物品编辑和装备编辑中的动态物品名、分类名、仓库名不属于 Trinity 的静态字符串。
构建脚本会从当前受支持游戏版本的原版 `iteminfo`、`ItemGroupInfo`、`InventoryInfo` 与简体中文 PALOC 中生成
行号锁定的中文回退表，并把目录译文所需字形合并进 ImGui 字体范围；只有 Trinity 自身的本地化
读取失败时才使用该回退，不覆盖原函数的成功结果。

## 构建

```powershell
& 'C:\Program Files\PowerShell\7\pwsh.exe' -NoLogo -NoProfile -ExecutionPolicy Bypass -File '.\tools\trinity_localizer\build_trinity_localizer.ps1'
```

构建产物固定为：

```text
dist/trinity_localizer/TrinityCN.asi
```

将其与 `Trinity.asi` 一起交给游戏的 ASI Loader 加载。当前实现严格匹配 Trinity `v0.13.2` 和 ImGui
`1.91.5`，动态目录数据严格匹配 Crimson Desert `1.17.00`。目标版本、字体函数序言、动态本地化
函数序言或游戏数据表哈希不一致时会自动拒绝对应功能，不修改 Trinity 的功能逻辑。

每次游戏启动都会在 `bin64/TrinityCN.log` 覆盖写入运行诊断，包括物品/分类地址表建立数量和
动态目录中文回退的首次命中结果；该日志不包含游戏存档或用户隐私数据。

旅行传送的顶层分类由 Trinity 在 `kSceneRules` 中硬编码，并非游戏本地化表字段；构建时按当前
0.13.2 二进制中逐项验证过的 UTF-8 字符串槽翻译。分类内地点仍可能来自场景 key、地区缩写、
固定场景名或游戏节点原名，需按各自生成模式分别处理。
