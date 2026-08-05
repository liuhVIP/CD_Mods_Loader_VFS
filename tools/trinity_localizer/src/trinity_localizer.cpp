// Trinity 0.13.1 中文伴生 ASI：运行时内嵌翻译映射与 ImGui 中文字体注入。
#include <Windows.h>

#include <algorithm>
#include <array>
#include <cctype>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <cwctype>
#include <limits>
#include <string>
#include <string_view>

#include "generated/translations.generated.h"

namespace trinity_cn {

// 当前适配对象为 Trinity 0.13.1；版本变化时必须重新确认函数入口和序言。
constexpr wchar_t kTargetModuleName[] = L"Trinity.asi";
constexpr wchar_t kTargetProcessName[] = L"CrimsonDesert.exe";
constexpr char kTargetVersion[] = "v0.13.1";
constexpr char kVersionLabel[] = "b站up 改名_汉化 v0.13.1";
constexpr std::uintptr_t kAddFontFromFileTtfRva = 0x65060;
constexpr std::uintptr_t kVersionTextLeaRva = 0x24F8E;
constexpr std::size_t kHookOverwriteSize = 17;
constexpr DWORD kModuleWaitMilliseconds = 30'000;
constexpr DWORD kModulePollMilliseconds = 10;

// Trinity 0.13.1 的 AddFontFromFileTTF 前 17 字节；不一致时拒绝安装 Hook。
constexpr std::array<std::uint8_t, kHookOverwriteSize> kExpectedFontFunctionPrologue{
    0x40, 0x55, 0x53, 0x56, 0x57, 0x41, 0x56, 0x41, 0x57,
    0x48, 0x8D, 0xAC, 0x24, 0x38, 0xFF, 0xFF, 0xFF,
};

// 唯一版本文本引用：lea rsi, [rip + v0.13.1]。
constexpr std::array<std::uint8_t, 7> kExpectedVersionTextLea{
    0x48, 0x8D, 0x35, 0xF3, 0x7F, 0x09, 0x00,
};

using AddFontFromFileTtfFn = void* (*)(
    void* atlas,
    const char* filename,
    float sizePixels,
    const void* fontConfig,
    const std::uint16_t* glyphRanges);

struct PeSectionView {
    std::uint8_t* address{};
    std::size_t size{};
};

HMODULE g_selfModule{};
AddFontFromFileTtfFn g_originalAddFontFromFileTtf{};
std::array<char, MAX_PATH> g_regularFontPath{};
std::array<char, MAX_PATH> g_boldFontPath{};

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

void DebugLog(std::string_view message) {
    std::string line = "[TrinityCN] ";
    line.append(message);
    line.push_back('\n');
    OutputDebugStringA(line.c_str());
}

HMODULE WaitForTargetModule() {
    DWORD elapsed = 0;
    while (elapsed < kModuleWaitMilliseconds) {
        if (HMODULE module = GetModuleHandleW(kTargetModuleName)) {
            return module;
        }
        Sleep(kModulePollMilliseconds);
        elapsed += kModulePollMilliseconds;
    }
    return nullptr;
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
    std::uint8_t* end = section.address + section.size - entry.capacity - 1;
    while (cursor <= end) {
        if (std::memcmp(cursor, entry.original, originalLength) != 0 || cursor[originalLength] != 0) {
            ++cursor;
            continue;
        }
        const bool paddingIsEmpty = std::all_of(
            cursor + originalLength,
            cursor + entry.capacity + 1,
            [](std::uint8_t value) { return value == 0; });
        if (!paddingIsEmpty) {
            ++cursor;
            continue;
        }
        std::string replacement(entry.capacity + 1, '\0');
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
        patchedCount += PatchTranslationEntry(readOnlyData, entry);
    }
    return patchedCount;
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

bool InstallFontHook(HMODULE module) {
    auto* target = reinterpret_cast<std::uint8_t*>(module) + kAddFontFromFileTtfRva;
    constexpr std::size_t jumpSize = 14;
    constexpr std::size_t trampolineSize = kHookOverwriteSize + jumpSize;
    auto* trampoline = static_cast<std::uint8_t*>(VirtualAlloc(
        nullptr, trampolineSize, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE));
    if (trampoline == nullptr) {
        return false;
    }
    std::memcpy(trampoline, target, kHookOverwriteSize);
    WriteAbsoluteJump(trampoline + kHookOverwriteSize, target + kHookOverwriteSize);
    g_originalAddFontFromFileTtf = reinterpret_cast<AddFontFromFileTtfFn>(trampoline);

    std::array<std::uint8_t, kHookOverwriteSize> patch{};
    patch.fill(0x90);
    WriteAbsoluteJump(patch.data(), reinterpret_cast<const void*>(&HookedAddFontFromFileTtf));
    if (!WriteMemory(target, patch.data(), patch.size())) {
        g_originalAddFontFromFileTtf = nullptr;
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

DWORD WINAPI InitializeLocalization(void*) {
    HMODULE trinityModule = WaitForTargetModule();
    if (trinityModule == nullptr) {
        DebugLog("等待 Trinity.asi 超时，未执行任何修改。");
        return 0;
    }
    if (!ValidateTargetVersion(trinityModule)) {
        return 0;
    }
    if (!BuildChineseFontPaths()) {
        DebugLog("未找到微软雅黑字体，未执行文本替换。");
        return 0;
    }
    if (!InstallFontHook(trinityModule)) {
        DebugLog("安装 Trinity 字体 Hook 失败，未执行文本替换。");
        return 0;
    }
    RedirectVersionLabel(trinityModule);
    const std::size_t patchedCount = ApplyEmbeddedTranslations(trinityModule);
    if (patchedCount == 0) {
        DebugLog("没有匹配到可替换文本。");
        return 0;
    }
    DebugLog("运行时中文映射已启用。");
    return 0;
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
