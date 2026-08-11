# NeoEyes 运行时只读检测器：检查模块加载状态、GdipDrawString IAT 与跳板 Hook。
[CmdletBinding()]
param(
    [int]$ProcessId = 0,
    [switch]$Watch,
    [ValidateRange(200, 10000)]
    [int]$IntervalMilliseconds = 1000
)

$ErrorActionPreference = 'Stop'

Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Runtime.InteropServices;

public static class NeoEyesRuntimeNative
{
    private const uint TH32CS_SNAPMODULE = 0x00000008;
    private const uint TH32CS_SNAPMODULE32 = 0x00000010;
    private const uint PROCESS_VM_READ = 0x0010;
    private const uint PROCESS_QUERY_INFORMATION = 0x0400;
    private static readonly IntPtr InvalidHandleValue = new IntPtr(-1);

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct MODULEENTRY32
    {
        public uint dwSize;
        public uint th32ModuleID;
        public uint th32ProcessID;
        public uint GlblcntUsage;
        public uint ProccntUsage;
        public IntPtr modBaseAddr;
        public uint modBaseSize;
        public IntPtr hModule;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 256)]
        public string szModule;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)]
        public string szExePath;
    }

    public sealed class ModuleInfo
    {
        public string Name = string.Empty;
        public string Path = string.Empty;
        public ulong BaseAddress;
        public uint Size;
    }

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr CreateToolhelp32Snapshot(uint flags, uint processId);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool Module32FirstW(IntPtr snapshot, ref MODULEENTRY32 entry);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool Module32NextW(IntPtr snapshot, ref MODULEENTRY32 entry);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CloseHandle(IntPtr handle);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr OpenProcess(uint access, bool inheritHandle, uint processId);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool ReadProcessMemory(
        IntPtr process,
        IntPtr address,
        [Out] byte[] buffer,
        UIntPtr size,
        out UIntPtr bytesRead);

    public static ModuleInfo[] EnumerateModules(uint processId)
    {
        IntPtr snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, processId);
        if (snapshot == InvalidHandleValue)
            throw new Win32Exception(Marshal.GetLastWin32Error(), "无法创建模块快照");
        try
        {
            var modules = new List<ModuleInfo>();
            var entry = new MODULEENTRY32();
            entry.dwSize = (uint)Marshal.SizeOf<MODULEENTRY32>();
            if (!Module32FirstW(snapshot, ref entry))
                throw new Win32Exception(Marshal.GetLastWin32Error(), "无法读取首个模块");
            do
            {
                modules.Add(new ModuleInfo
                {
                    Name = entry.szModule ?? string.Empty,
                    Path = entry.szExePath ?? string.Empty,
                    BaseAddress = unchecked((ulong)entry.modBaseAddr.ToInt64()),
                    Size = entry.modBaseSize,
                });
                entry.dwSize = (uint)Marshal.SizeOf<MODULEENTRY32>();
            }
            while (Module32NextW(snapshot, ref entry));
            return modules.ToArray();
        }
        finally
        {
            CloseHandle(snapshot);
        }
    }

    public static byte[] ReadBytes(uint processId, ulong address, int count)
    {
        IntPtr process = OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, false, processId);
        if (process == IntPtr.Zero)
            throw new Win32Exception(Marshal.GetLastWin32Error(), "无法打开游戏进程进行只读检查");
        try
        {
            byte[] buffer = new byte[count];
            if (!ReadProcessMemory(
                    process,
                    unchecked(new IntPtr((long)address)),
                    buffer,
                    new UIntPtr((uint)count),
                    out UIntPtr bytesRead) || bytesRead.ToUInt64() != (ulong)count)
                throw new Win32Exception(Marshal.GetLastWin32Error(), "读取游戏进程内存失败");
            return buffer;
        }
        finally
        {
            CloseHandle(process);
        }
    }
}
'@

# 目标 1.2.7 样本中已重新确认的固定 RVA。
$neoEyesModuleName = 'NeoEyesSimpleMenu.asi'
$companionModulePrefix = 'NeoEyesCN'
$drawThunkRva = [uint64]0x14248
$drawImportSlotRva = [uint64]0x17408
$originalThunkBytes = 'FF25BA310000'

function ConvertTo-HexText {
    param([Parameter(Mandatory)][byte[]]$Bytes)
    return ($Bytes | ForEach-Object { $_.ToString('X2') }) -join ''
}

function Get-TargetProcess {
    if ($ProcessId -gt 0) {
        return Get-Process -Id $ProcessId -ErrorAction Stop
    }
    $processes = @(Get-Process -Name 'CrimsonDesert' -ErrorAction SilentlyContinue)
    if ($processes.Count -ne 1) {
        throw "需要且只能有一个 CrimsonDesert 进程，当前数量：$($processes.Count)"
    }
    return $processes[0]
}

function Test-AddressInModule {
    param(
        [Parameter(Mandatory)][uint64]$Address,
        [Parameter(Mandatory)]$Module
    )
    return $Address -ge $Module.BaseAddress -and
        $Address -lt ($Module.BaseAddress + [uint64]$Module.Size)
}

function Show-NeoEyesRuntimeState {
    $process = Get-TargetProcess
    $modules = @([NeoEyesRuntimeNative]::EnumerateModules([uint32]$process.Id))
    $target = $modules | Where-Object { $_.Name -ieq $neoEyesModuleName } | Select-Object -First 1
    $companion = $modules | Where-Object { $_.Name -like "$companionModulePrefix*.asi" } | Select-Object -First 1

    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] PID=$($process.Id) 模块数=$($modules.Count)"
    if (-not $target) {
        Write-Host '  NeoEyesSimpleMenu.asi：模块快照中未发现' -ForegroundColor Red
        return
    }
    Write-Host ('  NeoEyesSimpleMenu.asi：0x{0:X}，大小 0x{1:X}' -f $target.BaseAddress, $target.Size)
    if ($companion) {
        Write-Host ('  {0}：0x{1:X}，大小 0x{2:X}' -f $companion.Name, $companion.BaseAddress, $companion.Size)
    } else {
        Write-Host '  NeoEyesCN：模块快照中未发现，伴生 ASI 可能没有加载' -ForegroundColor Red
    }

    $thunkAddress = $target.BaseAddress + $drawThunkRva
    $importSlotAddress = $target.BaseAddress + $drawImportSlotRva
    $thunkBytes = [NeoEyesRuntimeNative]::ReadBytes([uint32]$process.Id, $thunkAddress, 6)
    $importBytes = [NeoEyesRuntimeNative]::ReadBytes([uint32]$process.Id, $importSlotAddress, 8)
    $thunkHex = ConvertTo-HexText -Bytes $thunkBytes
    $importPointer = [BitConverter]::ToUInt64($importBytes, 0)
    Write-Host ('  GdipDrawString IAT：0x{0:X}' -f $importPointer)
    Write-Host ('  绘制跳板字节：{0}' -f $thunkHex)

    if ($thunkHex -eq $originalThunkBytes) {
        Write-Host '  结论：绘制跳板仍是原始字节，直接 Hook 没有安装或已被恢复。' -ForegroundColor Red
        return
    }
    if ($thunkBytes[0] -ne 0xE9 -or $thunkBytes[5] -ne 0x90) {
        Write-Host '  结论：绘制跳板是未知状态，不能按当前版本解释。' -ForegroundColor Red
        return
    }

    $relative = [BitConverter]::ToInt32($thunkBytes, 1)
    $relayAddress = [uint64]([int64]$thunkAddress + 5 + $relative)
    $relayBytes = [NeoEyesRuntimeNative]::ReadBytes([uint32]$process.Id, $relayAddress, 12)
    $relayHex = ConvertTo-HexText -Bytes $relayBytes
    Write-Host ('  Relay：0x{0:X}，字节 {1}' -f $relayAddress, $relayHex)
    if ($relayBytes[0] -ne 0x48 -or $relayBytes[1] -ne 0xB8 -or
        $relayBytes[10] -ne 0xFF -or $relayBytes[11] -ne 0xE0) {
        Write-Host '  结论：Relay 字节异常。' -ForegroundColor Red
        return
    }
    $hookPointer = [BitConverter]::ToUInt64($relayBytes, 2)
    Write-Host ('  Hook 函数：0x{0:X}' -f $hookPointer)
    if ($companion -and -not (Test-AddressInModule -Address $hookPointer -Module $companion)) {
        Write-Host '  结论：Hook 地址不在伴生 ASI 模块范围内。' -ForegroundColor Red
        return
    }
    if ($importPointer -ne $hookPointer) {
        Write-Host '  结论：跳板已 Hook，但 IAT 指针不一致；守护线程可能未运行。' -ForegroundColor Yellow
        return
    }
    Write-Host '  结论：IAT、跳板和 Relay 均已安装；列表若仍为英文，实际文字未经过该 GdipDrawString 调用链。' -ForegroundColor Green
}

do {
    try {
        Show-NeoEyesRuntimeState
    } catch {
        Write-Host "检测失败：$($_.Exception.Message)" -ForegroundColor Red
    }
    if ($Watch) {
        Start-Sleep -Milliseconds $IntervalMilliseconds
    }
} while ($Watch)
