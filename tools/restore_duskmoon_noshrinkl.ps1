# Duskmoon 暗月布甲恢复作者 NoShrinkL 标签的单变量脚本。
# 仅修改游戏 mods 中已安装的 0380 Prefab，不修改下载源或其他服装资源。

param(
    [string]$GameDir = "G:\SteamLibrary\steamapps\common\Crimson Desert"
)

$ErrorActionPreference = "Stop"
$Prefab = Join-Path $GameDir "mods\tefolu-0376-guidemesh-crop-physics-v49-v48-thigh-root-hip-fix\files\0009\character\cd_phw_01_ub_00_0380.prefab"
$SourceTag = [Text.Encoding]::ASCII.GetBytes("Lowerbody")
$TargetTag = [Text.Encoding]::ASCII.GetBytes("NoShrinkL")

if (-not (Test-Path -LiteralPath $Prefab -PathType Leaf)) {
    throw "Duskmoon Prefab not found: $Prefab"
}

$Bytes = [IO.File]::ReadAllBytes($Prefab)
$Matches = [Collections.Generic.List[int]]::new()
for ($Offset = 0; $Offset -le $Bytes.Length - $SourceTag.Length; $Offset++) {
    $Matched = $true
    for ($Index = 0; $Index -lt $SourceTag.Length; $Index++) {
        if ($Bytes[$Offset + $Index] -ne $SourceTag[$Index]) {
            $Matched = $false
            break
        }
    }
    $HasStandaloneLengthPrefix =
        $Offset -ge 4 -and
        $Bytes[$Offset - 4] -eq $SourceTag.Length -and
        $Bytes[$Offset - 3] -eq 0 -and
        $Bytes[$Offset - 2] -eq 0 -and
        $Bytes[$Offset - 1] -eq 0
    if ($Matched -and $HasStandaloneLengthPrefix) {
        $Matches.Add($Offset)
    }
}
if ($Matches.Count -ne 1) {
    throw "Lowerbody match count is invalid: $($Matches.Count)"
}

[Array]::Copy($TargetTag, 0, $Bytes, $Matches[0], $TargetTag.Length)
[IO.File]::WriteAllBytes($Prefab, $Bytes)
$Hash = (Get-FileHash -LiteralPath $Prefab -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Host "Duskmoon 0380 Prefab restored to NoShrinkL: offset=$($Matches[0]) sha256=$Hash"
