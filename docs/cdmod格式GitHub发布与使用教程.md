# `.cdmod` 格式 GitHub 发布与使用教程

本文说明玩家如何安装 `.cdmod`，以及模组作者如何使用同版本的独立控制台转换器。

## 1. 玩家快速开始

1. 下载以 `.cdmod` 结尾的模组文件，不要解压。
2. 把文件放入游戏目录的 `mods`：

```text
G:\SteamLibrary\steamapps\common\Crimson Desert\mods
```

3. 运行与 `.cdmod` 版本一致的 VFS 加载器。

4. 等待加载器构建完成并启动游戏，然后在游戏内验证功能。

加载顺序保存在 `.cdloader/load_order.json`。数组中越靠下的模组加载越晚，覆盖优先级越高。临时禁用模组应写入 `.cdloader/disabled_mods.json`，不要只删除排序项，因为扫描时缺失项会自动补回。

更新或卸载时，替换或删除 `mods` 中对应的 `.cdmod` 即可。加载器会在下次扫描时同步排序和缓存状态，不需要手工修改游戏 PAZ。

## 2. `.cdmod` 到底是什么

`.cdmod` 不是“把旧模组改名为 ZIP”，也不是只服务于某一种 JSON。它是一个统一容器、能力声明协议和动态构建体系：

```text
.cdmod ZIP + manifest
-> 严格解析与组件校验
-> 按加载顺序形成多模组语义计划
-> table-specific writer / 资源适配器
-> 合成当前版本的 PABGB/PABGH/PALOC/资源
-> DMM-like PAZ/PAMT 分包
-> 统一生成 PATHC/PAPGT
-> VFS ASI 映射给游戏
```

它的核心价值不是扩展名，而是把“模组想改什么”和“当前游戏版本中该怎样落盘”分开。模组保存稳定的 key、字段路径、操作意图或必要资源；加载器在用户机器上读取当前游戏数据，合并所有模组后只生成一份最终结果。

当前体系可承载：

```text
semantic-patch       Format 3 / PABGB 字段级语义修改
localization-patch   PALOC 按 key 修改与动态语言选择
file-replacement     loose 完整文件替换
resource-transform   资源重定位、复制或结构变换
legacy-byte-patch    传统 JSON byte patch
archive-import       standalone 0.paz + 0.pamt
```

DDS/PATHC、WEM、PAA/PAC 等资源通过统一资源组件进入最终 overlay；模组自带的 PAPGT/PATHC 不会直接覆盖游戏 meta，而由加载器统一合成。

## 3. 已验证支持矩阵

“能扫描到”不等于“能生效”。下表只列已完成转换、构建并经过游戏内确认的代表案例：

| 类型 | 代表案例 | 已验证结果 |
| --- | --- | --- |
| Format 3 跨表语义补丁 | Equip Everything、No Fall Damage | 装备表、物品表、Buff 表组合生效 |
| Format 3 数组字段 | 4xAtkSpd | `statusinfo.stat_level_data[N]` 动态数组生效 |
| PALOC 动态本地化 | Display take and steal price | 根据活动语言从游戏原文重建并追加价格 |
| 角色资源变换 | K-Makeup for Cordelia | 头部资源映射到指定脸型后生效 |
| 动画资源替换 | Male Glide Animation | PAA 动画替换生效，膨胀压缩自动改为原始存储 |
| legacy visual 块复制 | Electro Mecha Longsword To Lightsaber | 修复不可见/崩溃后，武器视觉效果生效 |
| legacy JSON byte patch | 既有 Format 2 模组 | 与 loose、Format 3 按统一顺序合成 |
| loose / DDS / WEM / PAA / PAC | 已安装资源模组集合 | 进入 PAZ/PAMT；DDS 同步 PATHC |
| standalone PAZ/PAMT | 已安装独立归档模组 | 安全分配目录并注册到统一 PAPGT |

支持是按通用结构、目标表和 writer family 实现的，不是按具体模组名称硬编码。新模组命中已支持结构即可复用；新字段或新二进制结构仍需增加对应 writer 并验证。

## 4. 玩家能获得什么好处

### 4.1 单文件、统一安装

旧模组可能混合 `files/NNNN`、`gamedata`、JSON、meta 和 standalone PAZ/PAMT，放错一层就会失效。`.cdmod` 把 manifest、组件、依赖和资源集中在一个文件中，并由加载器选择正确安装路径。

### 4.2 更能抵抗游戏更新

传统 byte patch 依赖旧 offset；完整 PABGB/PALOC 替换会把旧版本整表覆盖回新游戏。语义 `.cdmod` 优先保存记录 key、字段路径和操作，在构建时以最新 vanilla 为基础重新定位。

这不是“永不失效”的承诺。游戏结构改变时，加载器可以明确报告字段不兼容，作者只需升级对应 writer 或组件，而不必让所有玩家手工覆盖旧整表。

### 4.3 多模组先合成，再交给游戏

loose、传统 JSON、Format 3 和资源变换不会分别覆盖同一个目标。加载器按最终顺序合成同一 entry，同一最终 PAMT 路径只保留优先级最高的结果。语义冲突还能定位到表、记录 key、字段和来源模组。

No Fall Damage 已验证了跨表依赖：`buffinfo` 定义效果，`iteminfo` 把 Buff 挂到装备；两张表必须共同成功才构成功能。对于同一 entry 内多个变长 intent，加载器先合成完整记录，再执行一次动态替换，并同步修正 PABGH。

### 4.4 更小的分发体积

PALOC 实测样本：

```text
旧完整中文 PALOC：15,288,641 字节
新语义 .cdmod：       1,576 字节
```

新包只保存 24 个 key 和追加 `({price})` 的规则，完整 PALOC 在玩家机器上从当前语言原版动态重建。

### 4.5 可验证、可缓存、可回退诊断

manifest、组件 schema 和 SHA-256 使输入可追踪。未变化分包可以直接复用；writer 会统计生成补丁、跳过项和原因。原生加速不可用时应保留正确的 Python fallback，速度可以下降，但结果不能变化。

## 5. PALOC 多语言如何工作

游戏包含多张语言 PALOC，各语言使用相同稳定 key、不同 value：

```text
localizationstring_eng.paloc
localizationstring_kor.paloc
localizationstring_jpn.paloc
localizationstring_zho-tw.paloc
localizationstring_zho-cn.paloc
```

语言无关组件只保存 key 和操作，例如：

```json
{
  "target": "gamedata/localizationstring_*.paloc",
  "language": "*",
  "changes": [
    {"key": "42597485641824", "op": "append", "suffix": " ({price})"}
  ]
}
```

VFS 构建时识别活动语言，读取该语言最新原版，按 key 修改，并只重建需要的 PALOC。因此中文保留中文原文，英文保留英文原文；文字来自游戏当前语言表，不是把作者的中文硬塞进所有语言。

活动语言按以下顺序识别：

1. `CDLOADER_LANGUAGE` 显式覆盖；
2. 与游戏目录匹配的 Steam appmanifest `language`；
3. Windows 系统区域回退。

活动语言写入 `vfs_state.json`，语言变化会让相关缓存失效。`append` 应保持幂等；`set` 必须带预期原文或语言限制，避免游戏更新后静默覆盖错误文本。

## 6. 模组作者如何转换

作者不应手写 PAMT、PAPGT、PATHC、entry hash 或压缩标志。推荐流程是：

```text
输入旧模组/修改后资源
-> 转换器识别组件类型与真实目标
-> 与当前 vanilla 比较
-> 输出 manifest 和最小必要数据
-> 严格解析回读
-> 冷构建与游戏内验证
```

从 GitHub Release 下载与加载器版本一致的转换器，例如：

```text
cdmod-converter-v5.zip
```

完整解压后双击：

```text
cdmod-converter-v5.exe
```

首次启动会要求选择界面语言：

```text
1. 中文
2. English
```

选择会保存在转换器目录的 `cdmod-converter.config.json`，后续启动直接使用该语言。
需要切换时可删除该配置重新选择，也可以在 CMD 中传入
`--language zh-CN` 或 `--language en-US`。

控制台支持：

```text
1. 转换单个模组
2. 批量转换 mods 目录
3. 退出
```

按提示输入或把路径拖入窗口：

```text
游戏根目录
旧模组文件或文件夹
.cdmod 输出目录
```

转换器不会删除或修改旧模组。批量模式会生成 `conversion-report.json`，作者应检查转换、部分转换、跳过和失败数量。转换完成后仍需使用同版本加载器在游戏内验证。

## 7. 兼容边界

Format 3 并不代表任意表、任意字段都能自动转换。转换器只处理当前版本明确支持的结构；无法确认的内容会报告为跳过或失败，而不是猜测写入。转换器和加载器必须使用同一个 GitHub Tag 下的版本。

## 8. 游戏更新后的处理原则

兼容结果应明确分类：

```text
EXACT         精确匹配当前游戏
MIGRATED      已按稳定 key/字段迁移
CONFLICT      与其他模组修改同一语义位置
INCOMPATIBLE  游戏结构或前置值已变化
```

加载器必须遵循：

- 始终从最新 vanilla PAZ/PAMT/PALOC/PABGB 重建，不拿旧 overlay 当原版；
- loose 作为 base，传统 JSON 叠加其上，Format 3 再叠加到最终内存结果；
- 同一 entry 的变长修改先整体合成，PABGB 长度变化同步修正 PABGH；
- DDS 根据最新 PAMT/PATHC 解析真实路径并更新 PATHC；
- standalone 归档安全分配编号，PAPGT/PATHC 由加载器统一生成；
- 关键前置条件或结构验证失败时明确跳过，禁止静默写入可疑字节；
- 加载顺序只来自最终 `scan_mods()` 顺序，不在 overlay 层另造规则。

`.cdmod` 的更新优势来自可定位、可迁移和集中升级 writer，不是承诺游戏大改后所有历史模组零维护永远有效。

## 9. 当前限制与诚实声明

- 已支持的 table writer 不代表 Crimson Desert 所有 Format 3 字段均自动支持；
- PALOC 已验证已有 key 的 `set` 和 `append`，新增/删除记录仍需单独验证；
- DDS、WEM、PAA、PAC 等二进制资源可以封装和替换，但通常无法像语义字段那样自动合并内容；
- ASI/native 插件可声明依赖、版本和冲突，但不能自动化解多个插件修改同一 Hook 点；
- `-AllowMissingTargets` 允许部分构建，不代表被跳过的功能已经生效；
- smoke test 只证明进程未立即退出，不能替代加载存档和实际游玩验证；
- 性能受模组数量、资源体积、磁盘、缓存状态和游戏版本影响，发布页必须说明基准条件。

## 10. 常见问题与排障

### 为什么 `.cdmod` 很小，构建后却有完整游戏文件？

容器保存修改意图或必要资源。加载器以当前游戏为基础生成游戏实际读取的完整文件，它们只存在于 VFS 工作区，不需要随模组重复分发。

### 为什么切换语言后需要重建？

每种语言是不同 PALOC。活动语言属于构建状态，变化后必须生成对应语言结果。

### 扫描成功为什么仍可能没有效果？

扫描只证明容器和组件可识别。还要确认目标能定位、writer 支持字段、没有被跳过、最终 entry 已进入 PAZ/PAMT，并在游戏内验证。重点查看“生成补丁”和“跳过”统计。

### 为什么不直接使用模组自带 meta？

某个模组的 PAPGT/PATHC 会覆盖其他模组注册信息。加载器必须把 JSON、loose、DDS、standalone 和语义分包统一合成 meta。

### 到哪里看日志？

优先查看：

```text
<游戏目录>\.cdloader\logs\cold_load.log
<游戏目录>\.cdloader\logs\hot_load.log
<游戏目录>\.cdloader\vfs_state.json
<游戏目录>\.cdloader\vfs_mapping_tree.json
<游戏目录>\.cdloader\vfs_runtime\logs\vfs_launcher.log
<游戏目录>\.cdloader\vfs_runtime\logs\vfs_runtime.log
```

游戏崩溃、存档无响应还应保存最新 `C:\Users\<用户>\AppData\Local\Pearl Abyss\log\Launcher_*.log` 和 Windows Application 事件。日志中“已识别为 cdmod”不是最终成功标志；应继续确认组件应用数、最终映射和游戏内效果。
