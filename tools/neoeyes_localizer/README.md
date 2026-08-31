# NeoEyes Simple Menu 1.5.0 运行时汉化（NeoEyesCN v1.5.1）

本工程生成独立的 `NeoEyesCN.asi`，不会修改原始 `NeoEyesSimpleMenu.asi`。伴生 ASI 在运行时严格
核对当前 1.5.0 样本的 PE 布局、代码特征和界面标记，然后替换内嵌 UTF-8 文本，并将 GDI+ 使用的
`Segoe UI`、`Consolas` 字体族切换为微软雅黑以显示中文。召唤目录名称在送入 UTF-8 绘制转换前按
`catalog_names.generated.h` 的当前游戏官方简中 PALOC 映射优先解析，未命中时再使用
`catalog_terms.zh-CN.json` 的组件词典。1.5.0 会提前缓存目录宽字符串，因此伴生补丁在最终
`GdipDrawString` 绘制入口替换屏幕显示文本，不改写用于搜索和召唤的原始英文 ID；末尾数字作为
游戏内部目录编号保留。官方名称映射来自当前 `CharacterInfo` 的本地化哈希和游戏简中 PALOC，
不是手工猜测的英文直译。

v0.9 额外把 `Animal_Desert_Fox_Wild_31410` 这类召唤目录 ID 在最终绘制阶段映射为
`动物·沙漠狐狸（野生） #31410`，因此按名称搜索进入的下一层菜单也会显示中文。

## 构建

```powershell
& 'C:\Program Files\PowerShell\7\pwsh.exe' -NoLogo -NoProfile -ExecutionPolicy Bypass -File '.\tools\neoeyes_localizer\build_neoeyes_localizer.ps1'
```

构建产物固定为：

```text
dist/neoeyes_localizer/NeoEyesCN.asi
```

运行中的 Hook 只读检查：

```powershell
& 'C:\Program Files\PowerShell\7\pwsh.exe' -NoLogo -NoProfile -ExecutionPolicy Bypass -File '.\tools\neoeyes_localizer\inspect_neoeyes_runtime.ps1'
```

v1.5.1 会在游戏 `bin64` 目录写入 `NeoEyesCNv1.5.1.runtime.log`，记录 Hook 安装结果和前 300 次
最终绘制文本，便于确认列表是否经过翻译入口。

批量提取运行中出现但词典尚未收录的词：

```powershell
& 'C:\Program Files\PowerShell\7\pwsh.exe' -NoLogo -NoProfile -ExecutionPolicy Bypass -File '.\tools\neoeyes_localizer\harvest_neoeyes_catalog.ps1' `
    -LogPath 'T:\C++\vfsDmoe\external_sandbox\overwrite\Data\bin64\neoeyescnv1.3.runtime.log' `
    -OutputPath '.\tools\neoeyes_localizer\neoeyes_catalog_candidates.json'
```

将 `NeoEyesCN.asi` 与原始 `NeoEyesSimpleMenu.asi` 一起放入游戏 ASI 加载目录。当前版本只支持
SHA-256 为 `D0036FE6755728DFC1C5531793477A62D66431CE2C49B916CED8DCD7B8D2E40D` 的 64 位样本
（v1.5.0）；目标二进制变化或 UTF-8 转换特征不一致时会自动停用，不修改 NPC 生成器的功能逻辑。

## 1.5.0 已知问题与更新边界

2026-08-31 用户实机确认 NeoEyesCN v1.5.1 可以显示中文，并可用小键盘 `NUMPAD 9` 正常打开
菜单。排障中同时确认 NeoEyes Simple Menu 1.5.0 原模组自身存在初始化异常：失败会导致
`NeoMini.log` 只停在 `[init] code`，没有 `[init] ready` 与
`[menu] listo. NUMPAD 9 abre el menu.`。伴生补丁因此必须避开 ASI Loader 和原模组初始化阶段，
完成缓冲后再安装显示层 Hook。

本版本还修复了固定显示翻译表声明数量大于实际初始化数量的问题。多出的空字符串条目会在菜单
首次绘制时让替换循环无限运行，表现为按 `NUMPAD 9` 后菜单不出现；对应回归程序必须在限定时间
内以退出码 0 完成，不能只以“构建成功”代替运行验证。

NeoEyes 原模组更新后，不得直接复用此 1.5.0 伴生 ASI。必须重新拉取新原版样本，记录哈希并
重新分析 PE 时间戳、镜像/节布局、UTF-8 转换、字体、GdipDrawString IAT/调用链、显示字符串槽和
初始化时序，再更新版本锁、重新生成官方中文目录、构建并实机验证。原模组自身的初始化故障也要
在无汉化补丁的对照环境中先单独验证，避免把上游功能故障误判为汉化问题。
