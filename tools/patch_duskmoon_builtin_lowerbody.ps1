# Duskmoon 暗月布甲内置 Lowerbody 规则单变量验证脚本。
# 仅等长替换已安装 0380 Prefab 中唯一的 NoShrinkL，不修改下载源或其他资源。

param(
    [string]$GameDir = "G:\SteamLibrary\steamapps\common\Crimson Desert"
)

$ErrorActionPreference = "Stop"

# Duskmoon 已安装模组目录及目标 Prefab。
$ModName = "tefolu-0376-guidemesh-crop-physics-v49-v48-thigh-root-hip-fix"
$PrefabRelativePath = "files\0009\character\cd_phw_01_ub_00_0380.prefab"
$PrefabPath = Join-Path (Join-Path (Join-Path $GameDir "mods") $ModName) $PrefabRelativePath

# 两个标签均为 9 字节，等长替换不会改变 Prefab 二进制布局。
$SourceTag = [Text.Encoding]::ASCII.GetBytes("NoShrinkL")
$TargetTag = [Text.Encoding]::ASCII.GetBytes("Lowerbody")

if (-not (Test-Path -LiteralPath $PrefabPath -PathType Leaf)) {
    throw "未找到 Duskmoon 0380 Prefab：$PrefabPath"
}

$Bytes = [IO.File]::ReadAllBytes($PrefabPath)
$Matches = [Collections.Generic.List[int]]::new()
for ($Offset = 0; $Offset -le $Bytes.Length - $SourceTag.Length; $Offset++) {
    $Equal = $true
    for ($Index = 0; $Index -lt $SourceTag.Length; $Index++) {
        if ($Bytes[$Offset + $Index] -ne $SourceTag[$Index]) {
            $Equal = $false
            break
        }
    }
    if ($Equal) {
        $Matches.Add($Offset)
    }
}

if ($Matches.Count -ne 1) {
    throw "NoShrinkL 命中次数异常：$($Matches.Count)，拒绝修改"
}

$TagOffset = $Matches[0]
for ($Index = 0; $Index -lt $TargetTag.Length; $Index++) {
    $Bytes[$TagOffset + $Index] = $TargetTag[$Index]
}
[IO.File]::WriteAllBytes($PrefabPath, $Bytes)

$Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $PrefabPath).Hash.ToLowerInvariant()
Write-Host "Duskmoon 0380 Prefab 已改用内置 Lowerbody：offset=$TagOffset sha256=$Hash"
