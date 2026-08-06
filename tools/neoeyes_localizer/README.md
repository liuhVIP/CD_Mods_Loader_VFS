# NeoEyes Simple Menu 1.2.4 运行时汉化

本工程生成独立的 `NeoEyesCN.asi`，不会修改原始 `NeoEyesSimpleMenu.asi`。伴生 ASI 在运行时严格
核对当前 1.2.4 样本的 PE 布局、代码特征和界面标记，然后替换内嵌 UTF-8 文本，并将 GDI+ 使用的
`Segoe UI`、`Consolas` 字体族切换为微软雅黑以显示中文。召唤目录名称在送入 UTF-8 绘制转换前按
`catalog_terms.zh-CN.json` 解析。1.2.4 会提前缓存目录宽字符串，因此伴生补丁在最终
`GdipDrawString` 绘制入口替换屏幕显示文本，不改写用于搜索和召唤的原始英文 ID；末尾数字作为
游戏内部目录编号保留。

## 构建

```powershell
& 'C:\Program Files\PowerShell\7\pwsh.exe' -NoLogo -NoProfile -ExecutionPolicy Bypass -File '.\tools\neoeyes_localizer\build_neoeyes_localizer.ps1'
```

构建产物固定为：

```text
dist/neoeyes_localizer/NeoEyesCN.asi
```

将 `NeoEyesCN.asi` 与原始 `NeoEyesSimpleMenu.asi` 一起放入游戏 ASI 加载目录。当前版本只支持
SHA-256 为 `632061165892B9744209B1CD8E872F364FDC2827DDE52ACC89F82C84B89B9B69` 的 64 位样本；
目标二进制变化或 UTF-8 转换特征不一致时会自动停用，不修改 NPC 生成器的功能逻辑。
