# 从 NeoEyes 运行日志提取所有实际绘制过的英文目录词，避免依赖逐层截图。
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$LogPath,
    [string]$OutputPath = '.\neoeyes_catalog_candidates.json'
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $LogPath -PathType Leaf)) {
    throw "日志不存在：$LogPath"
}

$catalogPath = Join-Path $PSScriptRoot 'catalog_terms.zh-CN.json'
$catalog = Get-Content -Raw -Encoding UTF8 -LiteralPath $catalogPath | ConvertFrom-Json
$known = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
foreach ($term in @($catalog.terms)) {
    [void]$known.Add([string]$term.source)
}
$ignored = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
foreach ($word in @(
    'NEO', 'EYES', 'Search', 'type', 'to', 'filter', 'BACKSPACE', 'spawn', 'exit',
    'owner', 'newest', 'on', 'top', 'select', 'apply', 'back', 'in', 'world',
    'online', 'move', 'enter', 'close', 'stats', 'not', 'found', 'b', 'up', 'v'
)) {
    [void]$ignored.Add($word)
}

$counts = @{}
foreach ($line in Get-Content -Encoding UTF8 -LiteralPath $LogPath) {
    if ($line -notmatch 'DISCOVER text=(.+)$') {
        continue
    }
    $text = $Matches[1]
    foreach ($token in [regex]::Matches($text, '[A-Za-z][A-Za-z0-9_]*')) {
        foreach ($part in [regex]::Matches($token.Value, '[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+')) {
            $source = $part.Value
            if ($source -match '^\d+$' -or $source.Length -lt 3 -or
                $source -cmatch '^[A-Z0-9]+$' -or $known.Contains($source) -or $ignored.Contains($source)) {
                continue
            }
            if (-not $counts.ContainsKey($source)) {
                $counts[$source] = 0
            }
            $counts[$source]++
        }
    }
}

$result = @($counts.GetEnumerator() | Sort-Object Name | ForEach-Object {
    [ordered]@{
        source = $_.Key
        occurrences = $_.Value
        translation = ''
        kind = 'core'
    }
})
[System.IO.File]::WriteAllText(
    (Resolve-Path (Split-Path -Parent $OutputPath)).Path + '\' + (Split-Path -Leaf $OutputPath),
    ($result | ConvertTo-Json -Depth 4),
    [System.Text.UTF8Encoding]::new($false))
Write-Host "提取候选词：$($result.Count) 条"
Write-Host "输出：$OutputPath"
