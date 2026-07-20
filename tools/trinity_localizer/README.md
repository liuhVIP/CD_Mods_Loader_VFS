# Trinity 0.10.0 运行时汉化

本工程生成独立的 `TrinityCN.asi`，不修改原始 `Trinity.asi`。英文到中文的映射只在开发阶段保存在
`translations.zh-CN.json`，构建时转换为 C++ 只读数据并编译进 ASI；发布目录禁止携带 JSON。
游戏内右上角版本号前、默认说明栏和 Windows 文件属性均带有 `b站up 改名_汉化` 标注。

## 构建

```powershell
& 'C:\Program Files\PowerShell\7\pwsh.exe' -NoLogo -NoProfile -ExecutionPolicy Bypass -File '.\tools\trinity_localizer\build_trinity_localizer.ps1'
```

构建产物固定为：

```text
dist/trinity_localizer/TrinityCN.asi
```

将其与 `Trinity.asi` 一起交给游戏的 ASI Loader 加载。当前实现严格匹配 Trinity `v0.10.0` 和 ImGui
`1.91.5`；目标版本或字体函数序言不一致时会自动停用，不修改 Trinity 的功能逻辑。
