# NeoEyes 中文伴生 ASI 构建脚本：校验目标样本和 UTF-8 字符串槽，再生成单一发布文件。
[CmdletBinding()]
param(
    [ValidateSet('Debug', 'Release')]
    [string]$Configuration = 'Release',
    [string]$NeoEyesSample = 'G:\NppMODdown\crimsondesert\NeoEyesSimpleMenuv1.2.7 3215 1 2026-08-08T02-58Z UHbidxlmY\NeoEyesSimpleMenu.asi',
    [string]$GameDir = 'G:\SteamLibrary\steamapps\common\Crimson Desert'
)

$ErrorActionPreference = 'Stop'
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = (Resolve-Path (Join-Path $scriptRoot '..\..')).Path
$translationPath = Join-Path $scriptRoot 'translations.zh-CN.json'
$catalogTermsPath = Join-Path $scriptRoot 'catalog_terms.zh-CN.json'
$generatedDirectory = Join-Path $scriptRoot 'src\generated'
$generatedHeader = Join-Path $generatedDirectory 'translations.generated.h'
$generatedCatalogHeader = Join-Path $generatedDirectory 'catalog_terms.generated.h'
$generatedCatalogNamesHeader = Join-Path $generatedDirectory 'catalog_names.generated.h'
$catalogNamesGenerator = Join-Path $scriptRoot 'generate_catalog_names.py'
$pythonExecutable = Join-Path $projectRoot '.venv\Scripts\python.exe'
$buildDirectory = Join-Path $scriptRoot 'build'
$releaseDirectory = Join-Path $projectRoot 'dist\neoeyes_localizer'
$sourceAsi = Join-Path $buildDirectory "$Configuration\NeoEyesCN.asi"
$catalogTestExecutable = Join-Path $buildDirectory "$Configuration\NeoEyesCatalogTranslationTest.exe"
$releaseAsi = Join-Path $releaseDirectory 'NeoEyesCN.asi'

# 当前 NeoEyes Simple Menu 1.2.7 样本哈希，用于阻止误绑其他版本。
$expectedSampleSha256 = '619FCFA0F54128227DCA152E6E36C2606C6A944DD1CBDB1567E8188CE9C17D80'

# 两处 UTF-8 转换调用的完整代码特征，均明确把代码页设为 65001。
$expectedUtf8Signatures = @(
    'C744242800100000448BCB488944242033D2B9E9FD0000FF1581910000',
    'C74424280010000033D24889442420B9E9FD00004889BC245820000041B9FFFFFFFFFF1575670000'
)

# GDI+ 最终文字绘制跳板：JMP [GdipDrawString IAT]，目录缓存文本必须在这里翻译。
$expectedGdipDrawStringThunkSignature = 'FF25BA310000'

function ConvertTo-CppByteLiteral {
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Value)
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Value)
    return '"' + (($bytes | ForEach-Object { '\x{0:X2}' -f $_ }) -join '') + '"'
}

function Get-FormatTokens {
    param([Parameter(Mandatory)][string]$Value)
    return [regex]::Matches($Value, '%(?:[-+ #0]*|\d+|\.|\*)*(?:hh|h|ll|l|j|z|t|L)?[A-Za-z%]') |
        ForEach-Object { $_.Value }
}

function Find-BytePatternOffsets {
    param(
        [Parameter(Mandatory)][byte[]]$Bytes,
        [Parameter(Mandatory)][byte[]]$Pattern
    )
    $offsets = [System.Collections.Generic.List[int]]::new()
    if ($Pattern.Length -eq 0 -or $Pattern.Length -gt $Bytes.Length) {
        return $offsets
    }
    for ($offset = 0; $offset -le $Bytes.Length - $Pattern.Length; $offset++) {
        $matched = $true
        for ($index = 0; $index -lt $Pattern.Length; $index++) {
            if ($Bytes[$offset + $index] -ne $Pattern[$index]) {
                $matched = $false
                break
            }
        }
        if ($matched) {
            $offsets.Add($offset)
        }
    }
    return $offsets
}

function ConvertFrom-HexString {
    param([Parameter(Mandatory)][string]$Value)
    if (($Value.Length % 2) -ne 0) {
        throw "十六进制特征长度无效：$Value"
    }
    $bytes = [byte[]]::new($Value.Length / 2)
    for ($index = 0; $index -lt $bytes.Length; $index++) {
        $bytes[$index] = [Convert]::ToByte($Value.Substring($index * 2, 2), 16)
    }
    return $bytes
}

function Get-PatchableTranslationSlotCount {
    param(
        [Parameter(Mandatory)][byte[]]$SampleBytes,
        [Parameter(Mandatory)][string]$Original,
        [Parameter(Mandatory)][int]$Capacity
    )
    $originalBytes = [System.Text.Encoding]::UTF8.GetBytes($Original)
    $pattern = [byte[]]::new($originalBytes.Length + 1)
    [Array]::Copy($originalBytes, $pattern, $originalBytes.Length)
    $patchableCount = 0
    foreach ($offset in (Find-BytePatternOffsets -Bytes $SampleBytes -Pattern $pattern)) {
        if ($offset + $Capacity -ge $SampleBytes.Length) {
            continue
        }
        $paddingIsEmpty = $true
        for ($index = $originalBytes.Length; $index -le $Capacity; $index++) {
            if ($SampleBytes[$offset + $index] -ne 0) {
                $paddingIsEmpty = $false
                break
            }
        }
        if ($paddingIsEmpty) {
            $patchableCount++
        }
    }
    return $patchableCount
}

if (-not (Test-Path -LiteralPath $NeoEyesSample -PathType Leaf)) {
    throw "缺少 NeoEyes 开发样本：$NeoEyesSample"
}
if (-not (Test-Path -LiteralPath $translationPath -PathType Leaf)) {
    throw "缺少开发翻译表：$translationPath"
}
if (-not (Test-Path -LiteralPath $catalogTermsPath -PathType Leaf)) {
    throw "缺少召唤目录词典：$catalogTermsPath"
}
if (-not (Test-Path -LiteralPath $catalogNamesGenerator -PathType Leaf)) {
    throw "缺少原版目录名称生成器：$catalogNamesGenerator"
}
if (-not (Test-Path -LiteralPath $pythonExecutable -PathType Leaf)) {
    throw "缺少项目 Python：$pythonExecutable"
}
if (-not (Test-Path -LiteralPath $GameDir -PathType Container)) {
    throw "缺少游戏目录：$GameDir"
}

$sampleHash = (Get-FileHash -LiteralPath $NeoEyesSample -Algorithm SHA256).Hash
if ($sampleHash -ne $expectedSampleSha256) {
    throw "NeoEyes 开发样本哈希不匹配：$sampleHash"
}
$sampleBytes = [System.IO.File]::ReadAllBytes($NeoEyesSample)
foreach ($signatureHex in $expectedUtf8Signatures) {
    $signature = ConvertFrom-HexString -Value $signatureHex
    $matches = @(Find-BytePatternOffsets -Bytes $sampleBytes -Pattern $signature)
    if ($matches.Count -ne 1) {
        throw "NeoEyes UTF-8 转换特征数量异常：$signatureHex，匹配 $($matches.Count) 处。"
    }
}
$drawThunkSignature = ConvertFrom-HexString -Value $expectedGdipDrawStringThunkSignature
$drawThunkMatches = @(Find-BytePatternOffsets -Bytes $sampleBytes -Pattern $drawThunkSignature)
if ($drawThunkMatches.Count -ne 1) {
    throw "NeoEyes GdipDrawString 跳板特征数量异常：预期 1，实际 $($drawThunkMatches.Count)。"
}

$translations = @(Get-Content -Raw -Encoding UTF8 -LiteralPath $translationPath | ConvertFrom-Json)
if ($translations.Count -eq 0) {
    throw '开发翻译表为空。'
}

# 召唤目录名称只在绘制前翻译；词典不得改写 NeoEyes 内部生成 ID。
$catalogConfig = Get-Content -Raw -Encoding UTF8 -LiteralPath $catalogTermsPath | ConvertFrom-Json
$catalogTerms = @($catalogConfig.terms)
if ($catalogTerms.Count -eq 0) {
    throw '召唤目录词典为空。'
}
$catalogKinds = @('ignore', 'category', 'core', 'modifier')
$seenCatalogTerms = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
foreach ($entry in $catalogTerms) {
    $source = [string]$entry.source
    $translation = [string]$entry.translation
    $kind = [string]$entry.kind
    if ($source -notmatch '^[A-Za-z][A-Za-z0-9]*$') {
        throw "召唤目录词条必须是 ASCII 标识片段：$source"
    }
    if (-not $seenCatalogTerms.Add($source)) {
        throw "发现重复召唤目录词条：$source"
    }
    if ($catalogKinds -notcontains $kind) {
        throw "召唤目录词条类型无效：$source -> $kind"
    }
    if ($kind -ne 'ignore' -and [string]::IsNullOrWhiteSpace($translation)) {
        throw "召唤目录译文不能为空：$source"
    }
}

$sampleText = [System.Text.Encoding]::ASCII.GetString($sampleBytes)
foreach ($probe in @($catalogConfig.smoke_tests)) {
    $source = [string]$probe.source
    $expectedText = [string]$probe.contains
    if ([string]::IsNullOrWhiteSpace($source) -or [string]::IsNullOrWhiteSpace($expectedText)) {
        throw '召唤目录冒烟测试配置不能为空。'
    }
    if (-not $sampleText.Contains($source, [System.StringComparison]::Ordinal)) {
        throw "NeoEyes 样本缺少召唤目录冒烟标识：$source"
    }
}
$seen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
foreach ($entry in $translations) {
    $original = [string]$entry.original
    $translation = [string]$entry.translation
    $capacity = [int]$entry.capacity
    if ([string]::IsNullOrWhiteSpace($original) -or [string]::IsNullOrWhiteSpace($translation)) {
        throw '翻译条目的 original/translation 不能为空。'
    }
    if (-not $seen.Add($original)) {
        throw "发现重复英文条目：$original"
    }
    $originalLength = [System.Text.Encoding]::UTF8.GetByteCount($original)
    $translationLength = [System.Text.Encoding]::UTF8.GetByteCount($translation)
    if ($capacity -lt $originalLength) {
        throw "翻译容量不能小于原字符串长度：$original"
    }
    if ($translationLength -gt $capacity) {
        throw "译文超过目标字符串槽：$original ($translationLength > $capacity)"
    }
    $originalTokens = @(Get-FormatTokens -Value $original)
    $translationTokens = @(Get-FormatTokens -Value $translation)
    if (($originalTokens -join '|') -ne ($translationTokens -join '|')) {
        throw "格式占位符不一致：$original"
    }
    $expectedOccurrences = if ($null -ne $entry.occurrences) { [int]$entry.occurrences } else { 1 }
    $actualOccurrences = Get-PatchableTranslationSlotCount `
        -SampleBytes $sampleBytes `
        -Original $original `
        -Capacity $capacity
    if ($expectedOccurrences -lt 1 -or $actualOccurrences -ne $expectedOccurrences) {
        throw "NeoEyes 样本中的字符串槽数量异常：$original，预期 $expectedOccurrences，实际 $actualOccurrences。"
    }
}

New-Item -ItemType Directory -Force -Path $generatedDirectory | Out-Null
$headerLines = [System.Collections.Generic.List[string]]::new()
$headerLines.Add('// 此文件由 build_neoeyes_localizer.ps1 生成，请勿手工修改。')
$headerLines.Add('#pragma once')
$headerLines.Add('#include <cstddef>')
$headerLines.Add('namespace neoeyes_cn::generated {')
$headerLines.Add('struct TranslationEntry { const char* original; const char* translation; std::size_t capacity; std::size_t occurrences; };')
$headerLines.Add('inline constexpr TranslationEntry kTranslations[] = {')
foreach ($entry in $translations) {
    $expectedOccurrences = if ($null -ne $entry.occurrences) { [int]$entry.occurrences } else { 1 }
    $headerLines.Add("    { $(ConvertTo-CppByteLiteral ([string]$entry.original)), $(ConvertTo-CppByteLiteral ([string]$entry.translation)), $([int]$entry.capacity), $expectedOccurrences },")
}
$headerLines.Add('};')
$headerLines.Add('inline constexpr std::size_t kTranslationCount = sizeof(kTranslations) / sizeof(kTranslations[0]);')
$headerLines.Add('inline constexpr std::size_t kExpectedPatchCount = [] { std::size_t count = 0; for (const auto& entry : kTranslations) { count += entry.occurrences; } return count; }();')
$headerLines.Add('}')
[System.IO.File]::WriteAllLines($generatedHeader, $headerLines, [System.Text.UTF8Encoding]::new($false))

$catalogHeaderLines = [System.Collections.Generic.List[string]]::new()
$catalogHeaderLines.Add('// 此文件由 build_neoeyes_localizer.ps1 生成，请勿手工修改。')
$catalogHeaderLines.Add('#pragma once')
$catalogHeaderLines.Add('#include <cstddef>')
$catalogHeaderLines.Add('namespace neoeyes_cn::generated {')
$catalogHeaderLines.Add('enum class CatalogTermKind { Ignore, Category, Core, Modifier };')
$catalogHeaderLines.Add('struct CatalogTerm { const char* source; const char* translation; CatalogTermKind kind; };')
$catalogHeaderLines.Add('inline constexpr CatalogTerm kCatalogTerms[] = {')
foreach ($entry in $catalogTerms) {
    $kind = switch ([string]$entry.kind) {
        'ignore' { 'CatalogTermKind::Ignore' }
        'category' { 'CatalogTermKind::Category' }
        'core' { 'CatalogTermKind::Core' }
        'modifier' { 'CatalogTermKind::Modifier' }
    }
    $catalogHeaderLines.Add("    { $(ConvertTo-CppByteLiteral ([string]$entry.source)), $(ConvertTo-CppByteLiteral ([string]$entry.translation)), $kind },")
}
$catalogHeaderLines.Add('};')
$catalogHeaderLines.Add('inline constexpr std::size_t kCatalogTermCount = sizeof(kCatalogTerms) / sizeof(kCatalogTerms[0]);')
$catalogHeaderLines.Add('}')
[System.IO.File]::WriteAllLines($generatedCatalogHeader, $catalogHeaderLines, [System.Text.UTF8Encoding]::new($false))

# 原版角色名称只在最终绘制边界使用，生成表不会进入 NeoEyes 的搜索或召唤容器。
& $pythonExecutable $catalogNamesGenerator `
    --game-dir $GameDir `
    --neoeyes-sample $NeoEyesSample `
    --output $generatedCatalogNamesHeader
if ($LASTEXITCODE -ne 0) { throw 'NeoEyes 原版目录名称生成失败。' }
$officialCatalogNameCount = @(
    Select-String -LiteralPath $generatedCatalogNamesHeader -Pattern '^    \{ '
).Count

cmake -S $scriptRoot -B $buildDirectory -G 'Visual Studio 17 2022' -A x64
if ($LASTEXITCODE -ne 0) { throw 'CMake 配置失败。' }
cmake --build $buildDirectory --config $Configuration --target NeoEyesCN --parallel
if ($LASTEXITCODE -ne 0) { throw 'NeoEyesCN 构建失败。' }
cmake --build $buildDirectory --config $Configuration --target NeoEyesCatalogTranslationTest --parallel
if ($LASTEXITCODE -ne 0) { throw 'NeoEyes 召唤目录翻译回归程序构建失败。' }
& $catalogTestExecutable
if ($LASTEXITCODE -ne 0) { throw 'NeoEyes 召唤目录翻译回归失败。' }
if (-not (Test-Path -LiteralPath $sourceAsi -PathType Leaf)) {
    throw "未找到构建产物：$sourceAsi"
}

New-Item -ItemType Directory -Force -Path $releaseDirectory | Out-Null
Get-ChildItem -LiteralPath $releaseDirectory -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -ne 'NeoEyesCN.asi' } |
    ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }
Copy-Item -LiteralPath $sourceAsi -Destination $releaseAsi -Force
$releaseFiles = @(Get-ChildItem -LiteralPath $releaseDirectory -File)
if ($releaseFiles.Count -ne 1 -or $releaseFiles[0].Name -ne 'NeoEyesCN.asi') {
    throw 'NeoEyes 发布目录必须只包含 NeoEyesCN.asi。'
}

$hash = Get-FileHash -LiteralPath $releaseAsi -Algorithm SHA256
Write-Host "构建完成：$releaseAsi"
Write-Host "内嵌翻译：$($translations.Count) 条"
Write-Host "召唤目录词典：$($catalogTerms.Count) 条"
Write-Host "原版目录名称：$officialCatalogNameCount 条"
Write-Host "SHA-256：$($hash.Hash)"
