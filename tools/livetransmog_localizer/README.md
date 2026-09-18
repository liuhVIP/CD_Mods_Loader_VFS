# Live Transmog 原版名称生成器

把 `CrimsonDesertLiveTransmog_display_names.tsv` 里的英文/AI 翻译显示名替换成游戏原版简中名称。
Live Transmog 的 TSV 第一列是 iteminfo 的内部 `string_key`(例如 `Aant_PlateArmor_Helm`)，
本工具按该 key 从当前游戏版本的原版 `iteminfo` 表与简中 PALOC(`gamedata/item.paloc`)中
提取官方中文名，只替换第二列；无官方名称的条目(QA/开发者物品等)必须由 `--fallback-tsv`
提供中文回退名，否则脚本直接报错。

## 用法

```powershell
& 'T:\python_pro\cdmm\.venv\Scripts\python.exe' `
  'T:\python_pro\cdmm\tools\livetransmog_localizer\generate_display_names.py' `
  --game-dir 'G:\SteamLibrary\steamapps\common\Crimson Desert' `
  --tsv-in  '<新版官方 TSV>' `
  --tsv-out '<输出中文 TSV>' `
  --fallback-tsv '<上一版中文 TSV，仅用于无官方名的条目>' `
  --report  '<名称变化明细，可选>'
```

生成后核对：行数、key 顺序、性别列(第 3 列 `Male`/`Female`)、行尾与 BOM 必须与原文件
完全一致，且第二列不能残留英文。随后备份原 TSV 并把新文件改名为标准文件名
`CrimsonDesertLiveTransmog_display_names.tsv` 即可被 mod 直接读取。

## 表结构说明（2.01.00 起）

- 数据表物理名：`*.pabgb/.pabgh` → `gamedata/binarystaticinfo__/bin/*.staticinfobody|*.staticinfoheader`；
  两种命名统一走加载器的 PAMT 逻辑查询(`get_game_pamt_index().find_best()`)，禁止再按旧物理路径拼名。
- 本地化文本：不再是单个 `localizationstring_zho-cn.paloc`，而是每种语言目录下按逻辑表拆分的
  39 个 `gamedata/*.paloc`；简中位于 `gamedata/stringtable/binary__/zho-cn`，物品名在
  `gamedata/item.paloc`(category 7)，通过 PAMT 的 `resolved_dir_path` 定位。
- iteminfo 记录布局：`[entry_id:u16|u32][string_key_len:u32][string_key][u8][u64]
  [LocalizableString: u8 category + u64 index + CString default]`，PALOC key 即十进制
  `index` 字符串。

## 当前锁定版本（游戏 2.02.00 / Live Transmog 0.15.0）

- 游戏 EXE `bin64\CrimsonDesert.exe` SHA-256：
  `BCBF623AD5690147DC462AEAED5B4F97BD73296BA0D6AB54663586E7088B1C0E`
- `iteminfo.pabgb`(=`gamedata/iteminfo.staticinfobody`)：
  `E646E4A0281930AEC1D6D750ACAAFE07740F60E0DF5242041FC5BF57AE7ABADE`
- `iteminfo.pabgh`(=`gamedata/iteminfo.staticinfoheader`)：
  `59F16D991F77876BB216C16AFFB50C3BBCBFEA9190602C9FDA1557A3E07123FE`
- `gamedata/item.paloc`(zho-cn)：
  `A59BE7815A1A3FF45777D9D5699BDD5BC8FBD59A700F6EA6A3BE23B00D715C00`

iteminfo 行数 6813。当前结果：6813 个 key 全部匹配 iteminfo string_key；
6741 个替换为官方简中名称，72 个无官方名称的 QA/Dev 条目保留上一版中文回退名。
`Visione_Chip_Hall` 的官方简中名在 2.02.00 被改为 `H.A.L.L.`，属于官方文本而非漏翻。

## 版本更新时

1. 新游戏版本：先重新锁定 EXE 与三张表哈希、iteminfo 行数；哈希不匹配时脚本会拒绝。
2. 新 Live Transmog 版本：先用官方 TSV 跑一遍，确认 key 与 iteminfo string_key 仍 100% 覆盖；
   新增 key 若在 iteminfo 中会自动替换，若出现 iteminfo 之外的 key 需要单独评估。
3. 重新生成、核对格式、对比差异后再发布；无官方名的条目必须带上中文回退名。

## 配套中文 ASI

显示名 TSV 只是数据文件，界面文字需要重编译汉化 ASI：以上游 tag
`live-transmog/v0.15.0` 为功能基线，套用仓库内的中文界面/字体补丁(3-way)，用 CMake/MSVC
x64 Release 重新编译，并确认产物哈希与官方 ASI 不同且包含中文界面串。