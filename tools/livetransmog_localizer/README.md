# Live Transmog 原版名称生成器

把 `CrimsonDesertLiveTransmog_display_names.tsv` 里的 AI 翻译显示名替换成游戏原版简中名称。
Live Transmog 的 TSV 第一列是 iteminfo 的内部 `string_key`（例如 `Aant_PlateArmor_Helm`），
本工具按该 key 从当前游戏版本的原版 `iteminfo.pabgb/.pabgh` 与 `localizationstring_zho-cn.paloc`
提取官方中文名，只替换第二列；无官方名称的条目（QA/开发者物品等）保留原显示名。

## 用法

```powershell
& 'T:\python_pro\cdmm\.venv\Scripts\python.exe' `
  'T:\python_pro\cdmm\tools\livetransmog_localizer\generate_display_names.py' `
  --game-dir 'G:\SteamLibrary\steamapps\common\Crimson Desert' `
  --tsv-in  'G:\...\CrimsonDesertLiveTransmog_display_names.tsv' `
  --tsv-out 'G:\...\CrimsonDesertLiveTransmog_display_names_new.tsv'
```

生成后核对：行数、key 顺序、性别列（第 3 列 `Male`/`Female`）、LF 行尾与无 BOM 必须与原文件
一致；随后备份原 TSV 并把新文件改名为标准文件名 `CrimsonDesertLiveTransmog_display_names.tsv`
即可被 mod 直接读取。

## 当前锁定版本（游戏 1.18.01）

- 游戏 EXE SHA-256：`974C0446CFCFB46AE11654FA39E34330157E83C1A3C767333820BEA7EEAFA30A`
- `iteminfo.pabgb`：`771FECB350BAA83BF77BB7BBD2756AEF8F7F47C96BB9A7F95E5539C7EB81C8D7`
- `iteminfo.pabgh`：`31D03AB14BA12797F1AD75A45766178EBBD52ACEA048FB8FFEACB9CFC30A1B16`
- `localizationstring_zho-cn.paloc`：`11A0A80CDB9D41F86DE11AABA14FB0638AA862264A0A3030D0FD0A769BE39E66`

当前结果：6573 个 key 全部匹配 iteminfo string_key；6501 个替换为官方简中名称，72 个
无官方名称（多为 `_QA`/`Dev_*` 物品）保留原显示名。

## 版本更新时

1. 新游戏版本先重新锁定 `iteminfo`/`paloc` 哈希并核对 EXE 哈希；哈希不匹配时脚本会拒绝。
2. 新 Live Transmog 版本先核对 TSV key 与 iteminfo string_key 的匹配率仍为 100%；若新增
   key 出现在 iteminfo 中会自动被替换，若出现 iteminfo 之外的 key 需要单独评估。
3. 重新生成、对比差异、保留格式后发布。
