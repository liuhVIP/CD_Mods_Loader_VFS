$src = 'G:\NppMODdown\crimsondesert\【服装汉化】清凉舞女长裙替换大地荣耀盔甲V1.18-cdmod\ZZZ - Earths Honor Armor to Goddess Dress - Dynamic Body Deep V Gold Belts-1.18.cdmod'
$out = 'G:\NppMODdown\crimsondesert\【服装汉化】清凉舞女长裙替换大地荣耀盔甲V1.18-cdmod\ZZZ - Earths Honor Armor to Goddess Dress - Dynamic Body Deep V Gold Belts-1.18.4-HidePants-NoShrinkBody.cdmod'
Add-Type -AssemblyName System.IO.Compression.FileSystem
$utf8 = [Text.UTF8Encoding]::new($false)
$readZip = [IO.Compression.ZipFile]::OpenRead($src)
$entries = @{}
foreach ($entry in $readZip.Entries) {
    $stream = $entry.Open()
    $ms = [IO.MemoryStream]::new()
    $stream.CopyTo($ms)
    $stream.Dispose()
    $entries[$entry.FullName] = $ms.ToArray()
}
$readZip.Dispose()

function Replace-UniqueShrinkTag {
    param(
        [byte[]]$Content,
        [string]$SourceTag
    )
    $source = [byte[]](@(9, 0, 0, 0) + $utf8.GetBytes($SourceTag))
    $target = [byte[]](@(9, 0, 0, 0) + $utf8.GetBytes('NoShrink_'))
    $matches = [Collections.Generic.List[int]]::new()
    for ($offset = 0; $offset -le $Content.Length - $source.Length; $offset++) {
        $equal = $true
        for ($index = 0; $index -lt $source.Length; $index++) {
            if ($Content[$offset + $index] -ne $source[$index]) {
                $equal = $false
                break
            }
        }
        if ($equal) { $matches.Add($offset) }
    }
    if ($matches.Count -ne 1) {
        throw "Prefab shrink tag 数量异常：$SourceTag count=$($matches.Count)"
    }
    $result = [byte[]]$Content.Clone()
    [Array]::Copy($target, 0, $result, $matches[0], $target.Length)
    return $result
}

$prefabPayloads = @(
    'assets/00003/cd_phw_00_ub_00_0163.prefab',
    'assets/00004/cd_phw_00_ub_00_0163_index01.prefab',
    'assets/00005/cd_phw_00_ub_00_0163_index02.prefab'
)
foreach ($payload in $prefabPayloads) {
    $content = Replace-UniqueShrinkTag -Content $entries[$payload] -SourceTag 'Lowerbody'
    $entries[$payload] = Replace-UniqueShrinkTag -Content $content -SourceTag 'Upperbody'
}

$manifest = [Text.Encoding]::UTF8.GetString($entries['manifest.json']) | ConvertFrom-Json
$manifest.version = '1.18.4-hide-pants-noshrink-body'
$manifest.name = "Earth's Honor Armor - Goddess Dress - Hidden Pants Full Body"
$manifest.description = '基于 1.18 基线，继续用全 LOD 索引退化隐藏原大地荣耀裤子与附属上身网格，同时把三份 0163 Prefab 的主上身、下身和附属上身收缩标签全部设为 NoShrink_。这样灰裤子不参与渲染，当前裸体体型也不会被挖空；其他装配、裙装、材质和体型配置保持不变。'
$manifest.components[0].file_count = 7
$entries['manifest.json'] = $utf8.GetBytes(($manifest | ConvertTo-Json -Depth 20))

$replacements = [Text.Encoding]::UTF8.GetString($entries['files/replacements.json']) | ConvertFrom-Json
foreach ($file in $replacements.files) {
    if ($file.payload -notin $prefabPayloads) { continue }
    $payload = $entries[$file.payload]
    $file.sha256 = [Convert]::ToHexString(
        [Security.Cryptography.SHA256]::HashData($payload)
    ).ToLowerInvariant()
    $file.size = $payload.Length
}
$entries['files/replacements.json'] = $utf8.GetBytes(($replacements | ConvertTo-Json -Depth 20))

$report = [Text.Encoding]::UTF8.GetString($entries['reports/conversion.json']) | ConvertFrom-Json
$report.summary.replacement_count = 8
$report.summary.static_replacement_count = 7
$report.summary.hidden_triangle_count = 4344
$report.summary.changed_index_count = 8688
$report.summary.hidden_pac_count = 2
$report.safety.geometry_policy = '完整保留 1.18 已验证上身/裙装几何，并继续用全 LOD 索引退化隐藏原 0163 下身裤子与附属上身网格；不打包裸体或腿 PAC。'
$report.safety.prefab_policy = '三份目标 Prefab 的组件、UID、PAC 路径和字节长度保持 1.18；主上身、下身与附属上身三个 shrink tag 均等长设为 NoShrink_。'
$entries['reports/conversion.json'] = $utf8.GetBytes(($report | ConvertTo-Json -Depth 20))

if (Test-Path -LiteralPath $out) { Remove-Item -LiteralPath $out -Force }
$writeZip = [IO.Compression.ZipFile]::Open($out, [IO.Compression.ZipArchiveMode]::Create)
foreach ($name in $entries.Keys) {
    $newEntry = $writeZip.CreateEntry($name, [IO.Compression.CompressionLevel]::Optimal)
    $dest = $newEntry.Open()
    $dest.Write($entries[$name], 0, $entries[$name].Length)
    $dest.Dispose()
}
$writeZip.Dispose()
Get-FileHash -LiteralPath $out -Algorithm SHA256 | Format-List
Get-Item -LiteralPath $out | Select-Object FullName,Length
