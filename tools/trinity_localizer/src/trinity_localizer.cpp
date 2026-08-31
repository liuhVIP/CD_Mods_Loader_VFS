// Trinity V1.3.2 VTweak 中文伴生 ASI：运行时内嵌翻译映射与 ImGui 中文字体注入。
#include <Windows.h>

#include <algorithm>
#include <array>
#include <cctype>
#include <cstddef>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <cwctype>
#include <limits>
#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>

#include "generated/catalog.generated.h"
#include "generated/translations.generated.h"

namespace trinity_cn {

// 当前适配对象为 Trinity V1.3.2 VTweak（2.00.01 / Lian fork）；版本变化时必须重新确认函数入口和序言。
constexpr wchar_t kTargetModuleName[] = L"Trinity.asi";
constexpr wchar_t kTargetProcessName[] = L"CrimsonDesert.exe";
constexpr char kTargetVersion[] = "v1.3.2 (vTweak by Lian)";
constexpr char kVersionLabel[] = "b站up 改名_汉化 v1.3.2";
constexpr char kCompanionVersion[] = "0.7.3.0";
constexpr std::uintptr_t kAddFontFromFileTtfRva = 0xA1C90;
constexpr std::uintptr_t kVersionTextLeaRva = 0x55990;
constexpr std::uintptr_t kCatalogLocStringRva = 0x34240;
constexpr std::uintptr_t kItemTableGlobalRva = 0x1CE200;
constexpr std::uintptr_t kGroupTableGlobalRva = 0x1CE208;
constexpr std::uintptr_t kInventoryTableGlobalRva = 0x1CE218;
constexpr std::uintptr_t kCatalogNamesHolderRva = 0x1CE220;
constexpr std::uintptr_t kItemTableLoadRva = 0x24667;
constexpr std::uintptr_t kGroupTableLoadRva = 0x25A34;
constexpr std::uintptr_t kStringInfoTableLoadRva = 0x354E9;
constexpr std::uintptr_t kDefinitionArrayLoadRva = 0x2DCFF;
constexpr std::uintptr_t kTableCountOffset = 0x08;
constexpr std::uintptr_t kTableDefinitionsOffset = 0x58;
constexpr std::uintptr_t kItemNameFieldOffset = 0x20;
constexpr std::uintptr_t kGroupNameFieldOffset = 0x18;
constexpr std::uintptr_t kInventoryNameFieldOffset = 0x70;
constexpr std::size_t kHookOverwriteSize = 17;
constexpr std::size_t kCatalogHookOverwriteSize = 23;
constexpr DWORD kModuleWaitMilliseconds = 60'000;
constexpr DWORD kModuleWaitHeartbeatMilliseconds = 3'000;
constexpr DWORD kModulePollMilliseconds = 10;

// Trinity V1.3.2 VTweak 的 AddFontFromFileTTF 前 17 字节；不一致时拒绝安装 Hook。
constexpr std::array<std::uint8_t, kHookOverwriteSize> kExpectedFontFunctionPrologue{
    0x40, 0x55, 0x53, 0x56, 0x57, 0x41, 0x56, 0x41, 0x57,
    0x48, 0x8D, 0xAC, 0x24, 0x38, 0xFF, 0xFF, 0xFF,
};

// 唯一版本文本引用：lea rdi, [rip + v1.3.2 (vTweak by Lian)]。
constexpr std::array<std::uint8_t, 7> kExpectedVersionTextLea{
    0x48, 0x8D, 0x3D, 0x39, 0x16, 0x12, 0x00,
};

// Trinity V1.3.2 VTweak 的名称 getter（0x34240）前 23 字节：栈保存指令加上
// 检查目录全局（0x1CE220）是否就绪的 cmp。覆盖长度必须结束在指令边界，
// 且跳板需要重定位其中的 RIP 相对操作数。
constexpr std::array<std::uint8_t, kCatalogHookOverwriteSize> kExpectedCatalogLocStringPrologue{
    0x48, 0x8B, 0xC4, 0x53, 0x55, 0x56, 0x57, 0x41, 0x56, 0x41, 0x57,
    0x48, 0x83, 0xEC, 0x38, 0x48, 0x83, 0x3D, 0xC9, 0x9F, 0x19, 0x00, 0x00,
};

// 当前 ItemInfo / ItemGroupInfo / stringinfo 全局与 definitions(+0x58) 的固定引用特征。
constexpr std::array<std::uint8_t, 7> kExpectedItemTableLoad{
    0x48, 0x8B, 0x0D, 0x92, 0x9B, 0x1A, 0x00,
};
constexpr std::array<std::uint8_t, 7> kExpectedGroupTableLoad{
    0x48, 0x8B, 0x0D, 0xCD, 0x87, 0x1A, 0x00,
};
constexpr std::array<std::uint8_t, 7> kExpectedStringInfoTableLoad{
    0x48, 0x8B, 0x0D, 0x28, 0x8D, 0x19, 0x00,
};
constexpr std::array<std::uint8_t, 4> kExpectedDefinitionArrayLoad{
    0x48, 0x8D, 0x4B, 0x58,
};

using AddFontFromFileTtfFn = void* (*)(
    void* atlas,
    const char* filename,
    float sizePixels,
    const void* fontConfig,
    const std::uint16_t* glyphRanges);
using CatalogLocStringFn = bool (*)(std::uintptr_t structAddress, char* output, std::size_t capacity);

struct PeSectionView {
    std::uint8_t* address{};
    std::size_t size{};
};

HMODULE g_selfModule{};
AddFontFromFileTtfFn g_originalAddFontFromFileTtf{};
CatalogLocStringFn g_originalCatalogLocString{};
std::array<char, MAX_PATH> g_regularFontPath{};
std::array<char, MAX_PATH> g_boldFontPath{};
SRWLOCK g_catalogTranslationLock = SRWLOCK_INIT;
SRWLOCK g_runtimeLogLock = SRWLOCK_INIT;
bool g_itemCatalogAddressesReady{};
bool g_groupCatalogAddressesReady{};
bool g_inventoryCatalogAddressesReady{};
bool g_itemCatalogFailureLogged{};
bool g_groupCatalogFailureLogged{};
bool g_inventoryCatalogFailureLogged{};
bool g_catalogFallbackLogged{};
std::unordered_map<std::uintptr_t, const char*> g_catalogTranslations;

bool EqualsInsensitive(std::wstring_view left, std::wstring_view right) {
    if (left.size() != right.size()) {
        return false;
    }
    return std::equal(left.begin(), left.end(), right.begin(), [](wchar_t a, wchar_t b) {
        return std::towlower(a) == std::towlower(b);
    });
}

std::wstring_view BaseName(std::wstring_view path) {
    const auto position = path.find_last_of(L"\\/");
    return position == std::wstring_view::npos ? path : path.substr(position + 1);
}

bool IsTargetProcess() {
    std::array<wchar_t, MAX_PATH> path{};
    const DWORD length = GetModuleFileNameW(nullptr, path.data(), static_cast<DWORD>(path.size()));
    if (length == 0 || length >= path.size()) {
        return false;
    }
    return EqualsInsensitive(BaseName(std::wstring_view(path.data(), length)), kTargetProcessName);
}

bool BuildRuntimeLogPath(std::wstring& output) {
    std::array<wchar_t, MAX_PATH> modulePath{};
    const DWORD length = GetModuleFileNameW(
        nullptr,
        modulePath.data(),
        static_cast<DWORD>(modulePath.size()));
    if (length == 0 || length >= modulePath.size()) {
        return false;
    }
    output.assign(modulePath.data(), length);
    const auto separator = output.find_last_of(L"\\/");
    output.replace(separator == std::wstring::npos ? 0 : separator + 1, std::wstring::npos, L"TrinityCN.log");
    return true;
}

void ResetRuntimeLog() {
    std::wstring path;
    if (!BuildRuntimeLogPath(path)) {
        return;
    }
    HANDLE file = CreateFileW(
        path.c_str(),
        GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        nullptr,
        CREATE_ALWAYS,
        FILE_ATTRIBUTE_NORMAL,
        nullptr);
    if (file != INVALID_HANDLE_VALUE) {
        CloseHandle(file);
    }
}

void DebugLog(std::string_view message) {
    std::string line = "[TrinityCN] ";
    line.append(message);
    line.append("\r\n");
    OutputDebugStringA(line.c_str());

    std::wstring path;
    if (!BuildRuntimeLogPath(path)) {
        return;
    }
    AcquireSRWLockExclusive(&g_runtimeLogLock);
    HANDLE file = CreateFileW(
        path.c_str(),
        FILE_APPEND_DATA,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        nullptr,
        OPEN_ALWAYS,
        FILE_ATTRIBUTE_NORMAL,
        nullptr);
    if (file != INVALID_HANDLE_VALUE) {
        DWORD written = 0;
        WriteFile(file, line.data(), static_cast<DWORD>(line.size()), &written, nullptr);
        CloseHandle(file);
    }
    ReleaseSRWLockExclusive(&g_runtimeLogLock);
}

HMODULE WaitForTargetModule() {
    DWORD elapsed = 0;
    bool heartbeatLogged = false;
    while (elapsed < kModuleWaitMilliseconds) {
        if (HMODULE module = GetModuleHandleW(kTargetModuleName)) {
            return module;
        }
        Sleep(kModulePollMilliseconds);
        elapsed += kModulePollMilliseconds;
        if (!heartbeatLogged && elapsed >= kModuleWaitHeartbeatMilliseconds) {
            heartbeatLogged = true;
            DebugLog("仍在等待 Trinity.asi 加载…");
        }
    }
    return nullptr;
}

std::string FormatHex(std::uintptr_t value) {
    char buffer[32] = {};
    _snprintf_s(buffer, _TRUNCATE, "0x%llX", static_cast<unsigned long long>(value));
    return std::string(buffer);
}

bool ReadPeSection(HMODULE module, std::string_view sectionName, PeSectionView& output) {
    if (module == nullptr) {
        return false;
    }
    auto* imageBase = reinterpret_cast<std::uint8_t*>(module);
    const auto* dosHeader = reinterpret_cast<const IMAGE_DOS_HEADER*>(imageBase);
    if (dosHeader->e_magic != IMAGE_DOS_SIGNATURE) {
        return false;
    }
    const auto* ntHeaders = reinterpret_cast<const IMAGE_NT_HEADERS64*>(imageBase + dosHeader->e_lfanew);
    if (ntHeaders->Signature != IMAGE_NT_SIGNATURE || ntHeaders->FileHeader.Machine != IMAGE_FILE_MACHINE_AMD64) {
        return false;
    }
    const IMAGE_SECTION_HEADER* section = IMAGE_FIRST_SECTION(ntHeaders);
    for (WORD index = 0; index < ntHeaders->FileHeader.NumberOfSections; ++index, ++section) {
        const std::string_view currentName(
            reinterpret_cast<const char*>(section->Name),
            strnlen_s(reinterpret_cast<const char*>(section->Name), IMAGE_SIZEOF_SHORT_NAME));
        if (currentName == sectionName) {
            output.address = imageBase + section->VirtualAddress;
            output.size = section->Misc.VirtualSize;
            return output.address != nullptr && output.size > 0;
        }
    }
    return false;
}

bool ContainsNullTerminatedString(const PeSectionView& section, std::string_view expected) {
    if (expected.empty() || section.size <= expected.size()) {
        return false;
    }
    const auto* begin = section.address;
    const auto* end = section.address + section.size - expected.size() - 1;
    for (const auto* cursor = begin; cursor <= end; ++cursor) {
        if (std::memcmp(cursor, expected.data(), expected.size()) == 0 && cursor[expected.size()] == 0) {
            return true;
        }
    }
    return false;
}

bool ValidateTargetVersion(HMODULE module) {
    PeSectionView readOnlyData{};
    if (!ReadPeSection(module, ".rdata", readOnlyData)) {
        DebugLog("无法读取 Trinity .rdata 节。");
        return false;
    }
    if (!ContainsNullTerminatedString(readOnlyData, kTargetVersion) ||
        !ContainsNullTerminatedString(readOnlyData, "Dye Equipment") ||
        !ContainsNullTerminatedString(readOnlyData, "ImGui 1.91.5 (19150)")) {
        DebugLog("Trinity 版本标识不匹配，已停用汉化。");
        return false;
    }
    const auto* target = reinterpret_cast<const std::uint8_t*>(module) + kAddFontFromFileTtfRva;
    if (!std::equal(kExpectedFontFunctionPrologue.begin(), kExpectedFontFunctionPrologue.end(), target)) {
        DebugLog("Trinity 字体函数特征不匹配，已停用汉化。");
        return false;
    }
    const auto* imageBase = reinterpret_cast<const std::uint8_t*>(module);
    if (!std::equal(
            kExpectedCatalogLocStringPrologue.begin(),
            kExpectedCatalogLocStringPrologue.end(),
            imageBase + kCatalogLocStringRva) ||
        !std::equal(
            kExpectedItemTableLoad.begin(),
            kExpectedItemTableLoad.end(),
            imageBase + kItemTableLoadRva) ||
        !std::equal(
            kExpectedGroupTableLoad.begin(),
            kExpectedGroupTableLoad.end(),
            imageBase + kGroupTableLoadRva) ||
        !std::equal(
            kExpectedStringInfoTableLoad.begin(),
            kExpectedStringInfoTableLoad.end(),
            imageBase + kStringInfoTableLoadRva) ||
        !std::equal(
            kExpectedDefinitionArrayLoad.begin(),
            kExpectedDefinitionArrayLoad.end(),
            imageBase + kDefinitionArrayLoadRva)) {
        DebugLog("Trinity 动态目录结构特征不匹配，已停用汉化。");
        return false;
    }
    return true;
}

bool WriteMemory(void* destination, const void* source, std::size_t size) {
    DWORD oldProtection = 0;
    if (!VirtualProtect(destination, size, PAGE_EXECUTE_READWRITE, &oldProtection)) {
        return false;
    }
    std::memcpy(destination, source, size);
    FlushInstructionCache(GetCurrentProcess(), destination, size);
    DWORD ignoredProtection = 0;
    VirtualProtect(destination, size, oldProtection, &ignoredProtection);
    return true;
}

void* AllocateNearAddress(const void* reference, std::size_t size) {
    SYSTEM_INFO systemInfo{};
    GetSystemInfo(&systemInfo);
    const auto referenceAddress = reinterpret_cast<std::uintptr_t>(reference);
    const auto minimumAddress = reinterpret_cast<std::uintptr_t>(systemInfo.lpMinimumApplicationAddress);
    const auto maximumAddress = reinterpret_cast<std::uintptr_t>(systemInfo.lpMaximumApplicationAddress);
    constexpr std::uintptr_t maximumDistance = 0x70000000;
    const std::uintptr_t searchStart = referenceAddress > maximumDistance
        ? std::max(minimumAddress, referenceAddress - maximumDistance)
        : minimumAddress;
    const std::uintptr_t searchEnd = referenceAddress < maximumAddress - maximumDistance
        ? referenceAddress + maximumDistance
        : maximumAddress;
    const std::uintptr_t allocationGranularity = systemInfo.dwAllocationGranularity;

    std::uintptr_t cursor = searchStart;
    while (cursor < searchEnd) {
        MEMORY_BASIC_INFORMATION information{};
        if (VirtualQuery(reinterpret_cast<const void*>(cursor), &information, sizeof(information)) == 0) {
            break;
        }
        const auto regionStart = reinterpret_cast<std::uintptr_t>(information.BaseAddress);
        const auto regionEnd = regionStart + information.RegionSize;
        if (information.State == MEM_FREE) {
            const auto candidate = (std::max(cursor, regionStart) + allocationGranularity - 1) &
                ~(allocationGranularity - 1);
            if (candidate < regionEnd && size <= regionEnd - candidate) {
                if (void* allocation = VirtualAlloc(
                        reinterpret_cast<void*>(candidate), size, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE)) {
                    return allocation;
                }
            }
        }
        if (regionEnd <= cursor) {
            break;
        }
        cursor = regionEnd;
    }
    return nullptr;
}

bool RedirectVersionLabel(HMODULE module) {
    auto* instruction = reinterpret_cast<std::uint8_t*>(module) + kVersionTextLeaRva;
    if (!std::equal(kExpectedVersionTextLea.begin(), kExpectedVersionTextLea.end(), instruction)) {
        DebugLog("右上角版本文本引用不匹配，已跳过汉化署名。");
        return false;
    }
    void* labelMemory = AllocateNearAddress(instruction, sizeof(kVersionLabel));
    if (labelMemory == nullptr) {
        DebugLog("无法在 Trinity 附近分配署名文本，已跳过右上角署名。");
        return false;
    }
    std::memcpy(labelMemory, kVersionLabel, sizeof(kVersionLabel));
    DWORD oldProtection = 0;
    VirtualProtect(labelMemory, sizeof(kVersionLabel), PAGE_READONLY, &oldProtection);

    const auto nextInstruction = reinterpret_cast<std::intptr_t>(instruction + kExpectedVersionTextLea.size());
    const auto displacement64 = reinterpret_cast<std::intptr_t>(labelMemory) - nextInstruction;
    if (displacement64 < std::numeric_limits<std::int32_t>::min() ||
        displacement64 > std::numeric_limits<std::int32_t>::max()) {
        VirtualFree(labelMemory, 0, MEM_RELEASE);
        DebugLog("署名文本距离超出 RIP 相对寻址范围。");
        return false;
    }
    const auto displacement = static_cast<std::int32_t>(displacement64);
    if (!WriteMemory(instruction + 3, &displacement, sizeof(displacement))) {
        VirtualFree(labelMemory, 0, MEM_RELEASE);
        DebugLog("写入右上角署名引用失败。");
        return false;
    }
    return true;
}

std::size_t PatchTranslationEntry(PeSectionView section, const generated::TranslationEntry& entry) {
    const std::size_t originalLength = std::strlen(entry.original);
    const std::size_t translatedLength = std::strlen(entry.translation);
    if (originalLength == 0 || entry.capacity < originalLength || translatedLength > entry.capacity ||
        section.size <= entry.capacity) {
        return 0;
    }
    std::size_t patchedCount = 0;
    std::uint8_t* cursor = section.address;
    std::uint8_t* end = section.address + section.size - entry.capacity;
    while (cursor <= end) {
        if (std::memcmp(cursor, entry.original, originalLength) != 0 || cursor[originalLength] != 0) {
            ++cursor;
            continue;
        }
        const bool paddingIsEmpty = std::all_of(
            cursor + originalLength,
            cursor + entry.capacity,
            [](std::uint8_t value) { return value == 0; });
        if (!paddingIsEmpty) {
            ++cursor;
            continue;
        }
        std::string replacement(entry.capacity, '\0');
        std::memcpy(replacement.data(), entry.translation, translatedLength);
        if (!WriteMemory(cursor, replacement.data(), replacement.size())) {
            return patchedCount;
        }
        ++patchedCount;
        cursor += entry.capacity + 1;
    }
    return patchedCount;
}

std::size_t ApplyEmbeddedTranslations(HMODULE module) {
    PeSectionView readOnlyData{};
    if (!ReadPeSection(module, ".rdata", readOnlyData)) {
        return 0;
    }
    std::size_t patchedCount = 0;
    for (const auto& entry : generated::kTranslations) {
        if (entry.rva == 0) {
            patchedCount += PatchTranslationEntry(readOnlyData, entry);
            continue;
        }
        auto* target = reinterpret_cast<std::uint8_t*>(module) + entry.rva;
        const std::size_t originalLength = std::strlen(entry.original);
        const std::size_t translatedLength = std::strlen(entry.translation);
        if (originalLength == 0 || entry.capacity < originalLength ||
            translatedLength > entry.capacity ||
            std::memcmp(target, entry.original, originalLength) != 0 ||
            !std::all_of(
                target + originalLength,
                target + entry.capacity,
                [](std::uint8_t value) { return value == 0; })) {
            DebugLog("定点分类翻译校验失败（RVA 0x" + FormatHex(entry.rva) + "）。");
            continue;
        }
        std::string replacement(entry.capacity, '\0');
        std::memcpy(replacement.data(), entry.translation, translatedLength);
        if (WriteMemory(target, replacement.data(), replacement.size())) {
            ++patchedCount;
        }
    }
    return patchedCount;
}

bool ReadCurrentProcessMemory(std::uintptr_t address, void* output, std::size_t size) {
    if (address == 0 || output == nullptr || size == 0) {
        return false;
    }
    SIZE_T bytesRead = 0;
    return ReadProcessMemory(
               GetCurrentProcess(),
               reinterpret_cast<const void*>(address),
               output,
               size,
               &bytesRead) != FALSE &&
        bytesRead == size;
}

bool BuildCatalogAddressTable(
    HMODULE trinityModule,
    std::uintptr_t trinityGlobalRva,
    std::uintptr_t localizedFieldOffset,
    std::uint32_t expectedRowCount,
    const generated_catalog::CatalogTranslation* translations,
    std::size_t translationCount,
    std::size_t& mappedCount) {
    mappedCount = 0;
    std::uintptr_t manager = 0;
    const std::uintptr_t managerSlot =
        reinterpret_cast<std::uintptr_t>(trinityModule) + trinityGlobalRva;
    if (!ReadCurrentProcessMemory(managerSlot, &manager, sizeof(manager)) || manager == 0) {
        return false;
    }

    std::uintptr_t table = 0;
    if (!ReadCurrentProcessMemory(manager, &table, sizeof(table)) || table == 0) {
        return false;
    }

    std::uint32_t rowCount = 0;
    if (!ReadCurrentProcessMemory(table + kTableCountOffset, &rowCount, sizeof(rowCount)) ||
        rowCount < expectedRowCount) {
        return false;
    }

    std::uintptr_t definitionsAddress = 0;
    if (!ReadCurrentProcessMemory(
            table + kTableDefinitionsOffset,
            &definitionsAddress,
            sizeof(definitionsAddress)) ||
        definitionsAddress == 0) {
        return false;
    }

    std::vector<std::uintptr_t> definitions(expectedRowCount);
    if (!ReadCurrentProcessMemory(
            definitionsAddress,
            definitions.data(),
            definitions.size() * sizeof(definitions.front()))) {
        return false;
    }

    for (std::size_t index = 0; index < translationCount; ++index) {
        const auto& entry = translations[index];
        if (entry.row >= expectedRowCount || definitions[entry.row] == 0) {
            continue;
        }
        g_catalogTranslations.insert_or_assign(
            definitions[entry.row] + localizedFieldOffset,
            entry.translation);
        ++mappedCount;
    }
    return true;
}

const char* FindCatalogTranslation(std::uintptr_t structAddress) {
    HMODULE trinityModule = GetModuleHandleW(kTargetModuleName);
    if (trinityModule == nullptr) {
        return nullptr;
    }

    AcquireSRWLockExclusive(&g_catalogTranslationLock);
    if (!g_itemCatalogAddressesReady) {
        std::size_t mappedCount = 0;
        g_itemCatalogAddressesReady = BuildCatalogAddressTable(
            trinityModule,
            kItemTableGlobalRva,
            kItemNameFieldOffset,
            generated_catalog::kExpectedItemRowCount,
            generated_catalog::kItemTranslations,
            generated_catalog::kItemTranslationCount,
            mappedCount);
        if (g_itemCatalogAddressesReady) {
            DebugLog("物品中文地址表已建立：" + std::to_string(mappedCount) + " 条。");
        } else if (!g_itemCatalogFailureLogged) {
            g_itemCatalogFailureLogged = true;
            DebugLog("物品中文地址表建立失败，将在后续请求中重试。");
        }
    }
    if (!g_groupCatalogAddressesReady) {
        std::size_t mappedCount = 0;
        g_groupCatalogAddressesReady = BuildCatalogAddressTable(
            trinityModule,
            kGroupTableGlobalRva,
            kGroupNameFieldOffset,
            generated_catalog::kExpectedGroupRowCount,
            generated_catalog::kGroupTranslations,
            generated_catalog::kGroupTranslationCount,
            mappedCount);
        if (g_groupCatalogAddressesReady) {
            DebugLog("分类中文地址表已建立：" + std::to_string(mappedCount) + " 条。");
        } else if (!g_groupCatalogFailureLogged) {
            g_groupCatalogFailureLogged = true;
            DebugLog("分类中文地址表建立失败，将在后续请求中重试。");
        }
    }
    if (!g_inventoryCatalogAddressesReady) {
        std::size_t mappedCount = 0;
        g_inventoryCatalogAddressesReady = BuildCatalogAddressTable(
            trinityModule,
            kInventoryTableGlobalRva,
            kInventoryNameFieldOffset,
            generated_catalog::kExpectedInventoryRowCount,
            generated_catalog::kInventoryTranslations,
            generated_catalog::kInventoryTranslationCount,
            mappedCount);
        if (g_inventoryCatalogAddressesReady) {
            DebugLog("仓库中文地址表已建立：" + std::to_string(mappedCount) + " 条。");
        } else if (!g_inventoryCatalogFailureLogged) {
            g_inventoryCatalogFailureLogged = true;
            DebugLog("仓库中文地址表建立失败，将在后续请求中重试。");
        }
    }
    const auto translation = g_catalogTranslations.find(structAddress);
    const char* result = translation == g_catalogTranslations.end() ? nullptr : translation->second;
    const bool logFallback = result != nullptr && !g_catalogFallbackLogged;
    if (logFallback) {
        g_catalogFallbackLogged = true;
    }
    ReleaseSRWLockExclusive(&g_catalogTranslationLock);
    if (logFallback) {
        DebugLog("动态目录中文回退已首次命中。");
    }
    return result;
}

bool CopyCatalogTranslation(const char* translation, char* output, std::size_t capacity) {
    if (translation == nullptr || output == nullptr || capacity == 0) {
        return false;
    }
    std::size_t copySize = std::min(std::strlen(translation), capacity - 1);
    while (copySize > 0 &&
           (static_cast<unsigned char>(translation[copySize]) & 0xC0U) == 0x80U) {
        --copySize;
    }
    std::memcpy(output, translation, copySize);
    output[copySize] = '\0';
    return copySize > 0;
}

bool HookedCatalogLocString(
    std::uintptr_t structAddress,
    char* output,
    std::size_t capacity) {
    if (const char* translation = FindCatalogTranslation(structAddress)) {
        if (CopyCatalogTranslation(translation, output, capacity)) {
            return true;
        }
    }
    if (g_originalCatalogLocString == nullptr) {
        return false;
    }
    // 名称池非空时，原 getter 会读取 Hook 声明中不存在的额外栈参数；此时不调用跳板，
    // 避免读取脏栈。作者本地化表未加载时该全局为空，原 getter 的快速失败路径可安全调用。
    std::uintptr_t namesHolder = 0;
    HMODULE module = GetModuleHandleW(kTargetModuleName);
    if (module != nullptr &&
        ReadCurrentProcessMemory(
            reinterpret_cast<std::uintptr_t>(module) + kCatalogNamesHolderRva,
            &namesHolder,
            sizeof(namesHolder)) &&
        namesHolder != 0) {
        return false;
    }
    return g_originalCatalogLocString(structAddress, output, capacity);
}

bool ContainsInsensitive(std::string_view value, std::string_view needle) {
    if (needle.empty() || value.size() < needle.size()) {
        return false;
    }
    for (std::size_t index = 0; index + needle.size() <= value.size(); ++index) {
        bool equal = true;
        for (std::size_t offset = 0; offset < needle.size(); ++offset) {
            const auto left = static_cast<unsigned char>(value[index + offset]);
            const auto right = static_cast<unsigned char>(needle[offset]);
            if (std::tolower(left) != std::tolower(right)) {
                equal = false;
                break;
            }
        }
        if (equal) {
            return true;
        }
    }
    return false;
}

void* HookedAddFontFromFileTtf(
    void* atlas,
    const char* filename,
    float sizePixels,
    const void* fontConfig,
    const std::uint16_t* glyphRanges) {
    if (g_originalAddFontFromFileTtf == nullptr || filename == nullptr) {
        return nullptr;
    }
    const std::string_view requestedFont(filename);
    if (ContainsInsensitive(requestedFont, "segoeui.ttf")) {
        return g_originalAddFontFromFileTtf(
            atlas, g_regularFontPath.data(), sizePixels, fontConfig, generated::kGlyphRanges);
    }
    if (ContainsInsensitive(requestedFont, "seguisb.ttf") ||
        ContainsInsensitive(requestedFont, "segoeuib.ttf")) {
        return g_originalAddFontFromFileTtf(
            atlas, g_boldFontPath.data(), sizePixels, fontConfig, generated::kGlyphRanges);
    }
    return g_originalAddFontFromFileTtf(atlas, filename, sizePixels, fontConfig, glyphRanges);
}

void WriteAbsoluteJump(std::uint8_t* destination, const void* target) {
    destination[0] = 0xFF;
    destination[1] = 0x25;
    destination[2] = 0x00;
    destination[3] = 0x00;
    destination[4] = 0x00;
    destination[5] = 0x00;
    const auto targetAddress = reinterpret_cast<std::uintptr_t>(target);
    std::memcpy(destination + 6, &targetAddress, sizeof(targetAddress));
}

template <std::size_t overwriteSize, typename FunctionPointer>
bool InstallAbsoluteHook(
    std::uint8_t* target,
    const std::array<std::uint8_t, overwriteSize>& expected,
    const void* hook,
    FunctionPointer& original) {
    static_assert(overwriteSize >= 14);
    if (!std::equal(expected.begin(), expected.end(), target)) {
        return false;
    }
    constexpr std::size_t jumpSize = 14;
    constexpr std::size_t trampolineSize = overwriteSize + jumpSize;
    auto* trampoline = static_cast<std::uint8_t*>(VirtualAlloc(
        nullptr, trampolineSize, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE));
    if (trampoline == nullptr) {
        return false;
    }
    std::memcpy(trampoline, target, overwriteSize);
    WriteAbsoluteJump(trampoline + overwriteSize, target + overwriteSize);
    original = reinterpret_cast<FunctionPointer>(trampoline);

    std::array<std::uint8_t, overwriteSize> patch{};
    patch.fill(0x90);
    WriteAbsoluteJump(patch.data(), hook);
    if (!WriteMemory(target, patch.data(), patch.size())) {
        original = nullptr;
        VirtualFree(trampoline, 0, MEM_RELEASE);
        return false;
    }
    return true;
}

bool InstallFontHook(HMODULE module) {
    auto* target = reinterpret_cast<std::uint8_t*>(module) + kAddFontFromFileTtfRva;
    return InstallAbsoluteHook(
        target,
        kExpectedFontFunctionPrologue,
        reinterpret_cast<const void*>(&HookedAddFontFromFileTtf),
        g_originalAddFontFromFileTtf);
}

bool InstallCatalogTranslationHook(HMODULE module) {
    // V1.3.2 的 0x34240 序言包含一条 RIP 相对的 cmp（检查目录全局 0x1CE220），
    // 直接复制进跳板会读到跳板附近的未初始化内存。这里把 disp32 重定位到跳板自身，
    // 让回退调用仍然检查同一个绝对全局地址。
    auto* imageBase = reinterpret_cast<std::uint8_t*>(module);
    auto* target = imageBase + kCatalogLocStringRva;
    if (!std::equal(
            kExpectedCatalogLocStringPrologue.begin(),
            kExpectedCatalogLocStringPrologue.end(),
            target)) {
        DebugLog("Trinity 动态目录 Hook 序言不匹配，已跳过。");
        return false;
    }
    constexpr std::size_t jumpSize = 14;
    constexpr std::size_t trampolineSize = kCatalogHookOverwriteSize + jumpSize;
    // 跳板内重定位的 cmp 用 RIP 相对寻址访问目录就绪全局（imageBase + 0x1CE220），
    // 跳板必须落在模块 ±2GB 内，否则 disp32 溢出。AllocateNearAddress 返回
    // PAGE_READWRITE，复制内容后需转成 PAGE_EXECUTE_READWRITE 才能执行。
    auto* trampoline = static_cast<std::uint8_t*>(AllocateNearAddress(
        reinterpret_cast<const void*>(imageBase + kCatalogNamesHolderRva), trampolineSize));
    if (trampoline == nullptr) {
        DebugLog("无法在 Trinity 附近分配目录 Hook 跳板。");
        return false;
    }
    DWORD trampolineProtection = 0;
    if (!VirtualProtect(
            trampoline, trampolineSize, PAGE_EXECUTE_READWRITE, &trampolineProtection)) {
        DebugLog("无法将目录 Hook 跳板设为可执行。");
        VirtualFree(trampoline, 0, MEM_RELEASE);
        return false;
    }
    std::memcpy(trampoline, target, kCatalogHookOverwriteSize);
    // cmp 指令位于拷贝偏移 15，长度 8，其 RIP 为 trampoline + 23。
    const auto nextRip = reinterpret_cast<std::intptr_t>(trampoline + 15 + 8);
    const auto catalogReady = reinterpret_cast<std::intptr_t>(imageBase + kCatalogNamesHolderRva);
    const auto displacement64 = catalogReady - nextRip;
    if (displacement64 < std::numeric_limits<std::int32_t>::min() ||
        displacement64 > std::numeric_limits<std::int32_t>::max()) {
        DebugLog(
            "目录 Hook 跳板超出 RIP 相对寻址范围（catalogReady 0x" +
            FormatHex(static_cast<std::uintptr_t>(catalogReady)) + " trampoline 0x" +
            FormatHex(reinterpret_cast<std::uintptr_t>(trampoline)) + "）。");
        VirtualFree(trampoline, 0, MEM_RELEASE);
        return false;
    }
    const auto displacement = static_cast<std::int32_t>(displacement64);
    // cmp 的 disp32 位于指令起始偏移 15 + 3，而不是旧版本的偏移 14。
    std::memcpy(trampoline + 18, &displacement, sizeof(displacement));
    WriteAbsoluteJump(trampoline + kCatalogHookOverwriteSize, target + kCatalogHookOverwriteSize);
    g_originalCatalogLocString = reinterpret_cast<CatalogLocStringFn>(trampoline);

    std::array<std::uint8_t, kCatalogHookOverwriteSize> patch{};
    patch.fill(0x90);
    WriteAbsoluteJump(patch.data(), &HookedCatalogLocString);
    if (!WriteMemory(target, patch.data(), patch.size())) {
        g_originalCatalogLocString = nullptr;
        VirtualFree(trampoline, 0, MEM_RELEASE);
        return false;
    }
    return true;
}

bool BuildChineseFontPaths() {
    std::array<char, MAX_PATH> windowsDirectory{};
    const UINT length = GetWindowsDirectoryA(windowsDirectory.data(), static_cast<UINT>(windowsDirectory.size()));
    if (length == 0 || length >= windowsDirectory.size()) {
        return false;
    }
    const std::string regular = std::string(windowsDirectory.data(), length) + "\\Fonts\\msyh.ttc";
    const std::string bold = std::string(windowsDirectory.data(), length) + "\\Fonts\\msyhbd.ttc";
    if (GetFileAttributesA(regular.c_str()) == INVALID_FILE_ATTRIBUTES ||
        GetFileAttributesA(bold.c_str()) == INVALID_FILE_ATTRIBUTES ||
        regular.size() >= g_regularFontPath.size() || bold.size() >= g_boldFontPath.size()) {
        return false;
    }
    std::copy(regular.begin(), regular.end(), g_regularFontPath.begin());
    std::copy(bold.begin(), bold.end(), g_boldFontPath.begin());
    return true;
}

void LogInitializationCrash(DWORD exceptionCode) {
    char buffer[160] = {};
    _snprintf_s(
        buffer,
        _TRUNCATE,
        "初始化线程异常，汉化安装中断（code=0x%08X）。",
        static_cast<unsigned int>(exceptionCode));
    DebugLog(buffer);
}

DWORD WINAPI InitializeLocalizationImpl(void*) {
    const ULONGLONG startedAt = GetTickCount64();
    const auto progressLog = [&](const char* step) {
        DebugLog(
            std::string(step) + "（启动后 " +
            std::to_string(GetTickCount64() - startedAt) + " ms）");
    };
    ResetRuntimeLog();
    DebugLog(std::string("TrinityCN ") + kCompanionVersion + " 初始化。");
    HMODULE trinityModule = WaitForTargetModule();
    if (trinityModule == nullptr) {
        DebugLog("等待 Trinity.asi 超时，未执行任何修改。");
        return 0;
    }
    progressLog("已定位 Trinity.asi，开始版本校验");
    if (!ValidateTargetVersion(trinityModule)) {
        return 0;
    }
    progressLog("版本校验通过，开始准备中文字体");
    if (!BuildChineseFontPaths()) {
        DebugLog("未找到微软雅黑字体，未执行文本替换。");
        return 0;
    }
    progressLog("中文字体就绪，安装字体 Hook");
    if (!InstallFontHook(trinityModule)) {
        DebugLog("安装 Trinity 字体 Hook 失败，未执行文本替换。");
        return 0;
    }
    progressLog("字体 Hook 已安装，安装动态目录中文 Hook");
    if (!InstallCatalogTranslationHook(trinityModule)) {
        DebugLog("安装 Trinity 动态目录中文 Hook 失败，物品与分类名称保持原样。");
    }
    progressLog("目录 Hook 处理完成，写入右上角署名");
    RedirectVersionLabel(trinityModule);
    progressLog("署名写入完成，应用内嵌静态翻译");
    const std::size_t patchedCount = ApplyEmbeddedTranslations(trinityModule);
    if (patchedCount == 0) {
        DebugLog("没有匹配到可替换文本。");
        return 0;
    }
    progressLog("静态翻译应用完成");
    DebugLog("运行时中文映射已启用。");
    return 0;
}

DWORD WINAPI InitializeLocalization(void*) {
    __try {
        return InitializeLocalizationImpl(nullptr);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        LogInitializationCrash(GetExceptionCode());
        return 1;
    }
}

}  // namespace trinity_cn

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID) {
    if (reason != DLL_PROCESS_ATTACH) {
        return TRUE;
    }
    trinity_cn::g_selfModule = module;
    DisableThreadLibraryCalls(module);
    if (!trinity_cn::IsTargetProcess()) {
        return TRUE;
    }
    HANDLE thread = CreateThread(nullptr, 0, trinity_cn::InitializeLocalization, nullptr, 0, nullptr);
    if (thread != nullptr) {
        CloseHandle(thread);
    }
    return TRUE;
}
