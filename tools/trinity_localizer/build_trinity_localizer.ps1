# Trinity 中文伴生 ASI 构建脚本：校验开发翻译表、生成内嵌数据并产出单一 ASI。
[CmdletBinding()]
param(
    [ValidateSet('Debug', 'Release')]
    [string]$Configuration = 'Release',
    [string]$TrinitySample = 'G:\NppMODdown\crimsondesert\Trinity 0.13.2 CD1.17.00 3252 1 2026-08-10T14-41Z bJyGDfFfA\Trinity.asi',
    [string]$GameDir = 'G:\SteamLibrary\steamapps\common\Crimson Desert'
)

$ErrorActionPreference = 'Stop'
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = (Resolve-Path (Join-Path $scriptRoot '..\..')).Path
$translationPath = Join-Path $scriptRoot 'translations.zh-CN.json'
$catalogGenerator = Join-Path $scriptRoot 'generate_catalog_translations.py'
$generatedDirectory = Join-Path $scriptRoot 'src\generated'
$generatedHeader = Join-Path $generatedDirectory 'translations.generated.h'
$generatedCatalogHeader = Join-Path $generatedDirectory 'catalog.generated.h'
$generatedCatalogGlyphs = Join-Path $generatedDirectory 'catalog.glyphs.generated.txt'
$buildDirectory = Join-Path $scriptRoot 'build'
$releaseDirectory = Join-Path $projectRoot 'dist\trinity_localizer'
$sourceAsi = Join-Path $buildDirectory "$Configuration\TrinityCN.asi"
$releaseAsi = Join-Path $releaseDirectory 'TrinityCN.asi'
$projectPython = Join-Path $projectRoot '.venv\Scripts\python.exe'

# Trinity 0.13.2 的开发样本仅用于构建时核对英文条目，不会进入发布目录。
$expectedTrinityVersion = 'v0.13.2'
$expectedTrinitySha256 = '2CC92610604EDE9E75F9D884199D2EDE3382E1F26C0E99417F30ED357E179ADD'

function ConvertTo-CppByteLiteral {
    param([Parameter(Mandatory)][string]$Value)
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Value)
    return '"' + (($bytes | ForEach-Object { '\x{0:X2}' -f $_ }) -join '') + '"'
}

function Get-FormatTokens {
    param([Parameter(Mandatory)][string]$Value)
    return [regex]::Matches($Value, '%(?:[-+ #0]*|\d+|\.|\*)*(?:hh|h|ll|l|j|z|t|L)?[A-Za-z%]') |
        ForEach-Object { $_.Value }
}

function Test-PatchableTranslationSlot {
    param(
        [Parameter(Mandatory)][byte[]]$SampleBytes,
        [Parameter(Mandatory)][string]$SampleAscii,
        [Parameter(Mandatory)][string]$Original,
        [Parameter(Mandatory)][int]$Capacity
    )
    $originalLength = [System.Text.Encoding]::UTF8.GetByteCount($Original)
    $searchValue = "$Original`0"
    $searchOffset = 0
    while ($searchOffset -lt $SampleAscii.Length) {
        $matchOffset = $SampleAscii.IndexOf(
            $searchValue,
            $searchOffset,
            [System.StringComparison]::Ordinal
        )
        if ($matchOffset -lt 0) {
            return $false
        }
        $slotEnd = $matchOffset + $Capacity
        if ($slotEnd -lt $SampleBytes.Length) {
            $slotIsEmpty = $true
            for ($index = $matchOffset + $originalLength; $index -le $slotEnd; $index++) {
                if ($SampleBytes[$index] -ne 0) {
                    $slotIsEmpty = $false
                    break
                }
            }
            if ($slotIsEmpty) {
                return $true
            }
        }
        $searchOffset = $matchOffset + 1
    }
    return $false
}

if (-not (Test-Path -LiteralPath $translationPath -PathType Leaf)) {
    throw "缺少开发翻译表：$translationPath"
}

$translations = @(Get-Content -Raw -Encoding UTF8 -LiteralPath $translationPath | ConvertFrom-Json)
if ($translations.Count -eq 0) {
    throw '开发翻译表为空。'
}

$seen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
$sampleBytes = if (Test-Path -LiteralPath $TrinitySample -PathType Leaf) {
    $sampleHash = (Get-FileHash -LiteralPath $TrinitySample -Algorithm SHA256).Hash
    if ($sampleHash -ne $expectedTrinitySha256) {
        throw "Trinity 开发样本哈希不匹配：$sampleHash"
    }
    [System.IO.File]::ReadAllBytes($TrinitySample)
} else {
    $null
}
$sampleAscii = if ($null -ne $sampleBytes) {
    [System.Text.Encoding]::GetEncoding(28591).GetString($sampleBytes)
} else {
    $null
}
if ($null -ne $sampleAscii -and
    -not $sampleAscii.Contains("$expectedTrinityVersion`0", [System.StringComparison]::Ordinal)) {
    throw "Trinity 开发样本版本标识不匹配：$expectedTrinityVersion"
}
$glyphs = [System.Collections.Generic.SortedSet[int]]::new()
foreach ($codePoint in 0x20..0xFF) {
    [void]$glyphs.Add($codePoint)
}

foreach ($entry in $translations) {
    $original = [string]$entry.original
    $translation = [string]$entry.translation
    if ([string]::IsNullOrWhiteSpace($original) -or [string]::IsNullOrWhiteSpace($translation)) {
        throw '翻译条目的 original/translation 不能为空。'
    }
    if (-not $seen.Add($original)) {
        throw "发现重复英文条目：$original"
    }
    $originalLength = [System.Text.Encoding]::UTF8.GetByteCount($original)
    $translationLength = [System.Text.Encoding]::UTF8.GetByteCount($translation)
    $capacity = if ($null -ne $entry.capacity) { [int]$entry.capacity } else { $originalLength }
    if ($capacity -lt $originalLength) {
        throw "翻译容量不能小于原字符串长度：$original"
    }
    if ($translationLength -gt $capacity) {
        throw "译文超过原字符串空间：$original ($translationLength > $capacity)"
    }
    $originalTokens = @(Get-FormatTokens -Value $original)
    $translationTokens = @(Get-FormatTokens -Value $translation)
    if (($originalTokens -join '|') -ne ($translationTokens -join '|')) {
        throw "格式占位符不一致：$original"
    }
    if ($null -ne $sampleAscii -and -not $sampleAscii.Contains("$original`0", [System.StringComparison]::Ordinal)) {
        throw "Trinity 0.13.2 样本中不存在英文条目：$original"
    }
    if ($null -ne $sampleBytes -and -not (Test-PatchableTranslationSlot `
            -SampleBytes $sampleBytes `
            -SampleAscii $sampleAscii `
            -Original $original `
            -Capacity $capacity)) {
        throw "Trinity 0.13.2 样本中的字符串槽容量不足：$original ($capacity)"
    }
    foreach ($rune in $translation.EnumerateRunes()) {
        if ($rune.Value -gt 0xFFFF) {
            throw "当前 ImGui 字形表不支持补充平面字符：$translation"
        }
        [void]$glyphs.Add($rune.Value)
    }
}

# 动态物品和分类名称的全部字形必须一并写入 ImGui 字体范围，避免译文显示方框。
if (-not (Test-Path -LiteralPath $projectPython -PathType Leaf)) {
    throw "缺少项目 Python：$projectPython"
}
if (-not (Test-Path -LiteralPath $catalogGenerator -PathType Leaf)) {
    throw "缺少动态目录翻译生成器：$catalogGenerator"
}
& $projectPython $catalogGenerator `
    --game-dir $GameDir `
    --output $generatedCatalogHeader `
    --glyph-output $generatedCatalogGlyphs
if ($LASTEXITCODE -ne 0) { throw 'Trinity 动态目录中文生成失败。' }
$catalogGlyphText = Get-Content -Raw -Encoding UTF8 -LiteralPath $generatedCatalogGlyphs
foreach ($rune in $catalogGlyphText.EnumerateRunes()) {
    if ($rune.Value -gt 0xFFFF) {
        throw "当前 ImGui 字形表不支持动态目录中的补充平面字符：$rune"
    }
    [void]$glyphs.Add($rune.Value)
}

$ranges = [System.Collections.Generic.List[object]]::new()
$rangeStart = $null
$rangeEnd = $null
foreach ($glyph in $glyphs) {
    if ($null -eq $rangeStart) {
        $rangeStart = $glyph
        $rangeEnd = $glyph
        continue
    }
    if ($glyph -eq ($rangeEnd + 1)) {
        $rangeEnd = $glyph
        continue
    }
    $ranges.Add([PSCustomObject]@{ Start = $rangeStart; End = $rangeEnd })
    $rangeStart = $glyph
    $rangeEnd = $glyph
}
$ranges.Add([PSCustomObject]@{ Start = $rangeStart; End = $rangeEnd })

New-Item -ItemType Directory -Force -Path $generatedDirectory | Out-Null
$headerLines = [System.Collections.Generic.List[string]]::new()
$headerLines.Add('// 此文件由 build_trinity_localizer.ps1 生成，请勿手工修改。')
$headerLines.Add('#pragma once')
$headerLines.Add('#include <cstddef>')
$headerLines.Add('#include <cstdint>')
$headerLines.Add('namespace trinity_cn::generated {')
$headerLines.Add('struct TranslationEntry { const char* original; const char* translation; std::size_t capacity; };')
$headerLines.Add('inline constexpr TranslationEntry kTranslations[] = {')
foreach ($entry in $translations) {
    $originalLength = [System.Text.Encoding]::UTF8.GetByteCount([string]$entry.original)
    $capacity = if ($null -ne $entry.capacity) { [int]$entry.capacity } else { $originalLength }
    $headerLines.Add("    { $(ConvertTo-CppByteLiteral ([string]$entry.original)), $(ConvertTo-CppByteLiteral ([string]$entry.translation)), $capacity },")
}
$headerLines.Add('};')
$headerLines.Add('inline constexpr std::uint16_t kGlyphRanges[] = {')
foreach ($range in $ranges) {
    $headerLines.Add(('    0x{0:X4}, 0x{1:X4},' -f $range.Start, $range.End))
}
$headerLines.Add('    0x0000')
$headerLines.Add('};')
$headerLines.Add('inline constexpr std::size_t kTranslationCount = sizeof(kTranslations) / sizeof(kTranslations[0]);')
$headerLines.Add('}')
[System.IO.File]::WriteAllLines($generatedHeader, $headerLines, [System.Text.UTF8Encoding]::new($false))

cmake -S $scriptRoot -B $buildDirectory -G 'Visual Studio 17 2022' -A x64
if ($LASTEXITCODE -ne 0) { throw 'CMake 配置失败。' }
cmake --build $buildDirectory --config $Configuration --target TrinityCN --parallel
if ($LASTEXITCODE -ne 0) { throw 'TrinityCN 构建失败。' }
if (-not (Test-Path -LiteralPath $sourceAsi -PathType Leaf)) {
    throw "未找到构建产物：$sourceAsi"
}

New-Item -ItemType Directory -Force -Path $releaseDirectory | Out-Null
Copy-Item -LiteralPath $sourceAsi -Destination $releaseAsi -Force
$unexpectedFiles = @(Get-ChildItem -LiteralPath $releaseDirectory -File | Where-Object { $_.Name -ne 'TrinityCN.asi' })
if ($unexpectedFiles.Count -gt 0) {
    throw "发布目录存在非 ASI 文件：$($unexpectedFiles.Name -join ', ')"
}

$hash = Get-FileHash -LiteralPath $releaseAsi -Algorithm SHA256
Write-Host "构建完成：$releaseAsi"
Write-Host "内嵌翻译：$($translations.Count) 条"
Write-Host "字体字形：$($glyphs.Count) 个"
Write-Host "SHA-256：$($hash.Hash)"
