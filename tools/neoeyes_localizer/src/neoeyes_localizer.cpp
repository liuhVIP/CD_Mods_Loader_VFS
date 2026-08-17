// NeoEyes Simple Menu 1.4.0 中文伴生 ASI v1.4.1：优先显示游戏原版中文角色名称。
#include <Windows.h>

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <cwchar>
#include <cwctype>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "generated/catalog_names.generated.h"
#include "generated/catalog_terms.generated.h"
#include "generated/translations.generated.h"

namespace neoeyes_cn {

// 当前样本没有版本资源，必须通过模块名、PE 时间戳、布局、代码特征和界面标记共同锁定。
constexpr wchar_t kTargetModuleName[] = L"NeoEyesSimpleMenu.asi";
constexpr wchar_t kTargetProcessName[] = L"CrimsonDesert.exe";
constexpr DWORD kTargetPeTimestamp = 0x6A7E4343;
constexpr DWORD kTargetSizeOfImage = 0xC7000;
constexpr std::uintptr_t kFirstUtf8ConversionRva = 0x12482;
constexpr std::uintptr_t kSecondUtf8ConversionRva = 0x16F43;
constexpr std::uintptr_t kRegularFontReferenceRva = 0x72F9;
constexpr std::uintptr_t kMonospaceFontReferenceRva = 0x7316;
constexpr std::uintptr_t kGdipDrawStringThunkRva = 0x1AF38;
constexpr std::uintptr_t kGdipDrawStringImportSlotRva = 0x1D480;
constexpr DWORD kModuleWaitMilliseconds = 30'000;
constexpr DWORD kModulePollMilliseconds = 1;
constexpr DWORD kHookWarmupMilliseconds = 1'000;
constexpr DWORD kHookWatchdogPollMilliseconds = 250;
constexpr std::size_t kMaximumRenderedTextBytes = 4'096;
constexpr LONG kMaximumDrawDiagnostics = 300;
constexpr std::size_t kMaximumDiscoveredDrawTexts = 8'192;

using GdipDrawStringFunction = int(WINAPI*)(
    void*, const wchar_t*, int, const void*, const void*, const void*, const void*);
GdipDrawStringFunction gOriginalGdipDrawString = nullptr;
ULONGLONG* gGdipDrawStringImportSlot = nullptr;
std::uint8_t* gGdipDrawStringThunkAddress = nullptr;
std::uint8_t* gGdipDrawStringRelay = nullptr;
volatile LONG gHookWatchdogStarted = 0;
volatile LONG gDrawDiagnosticCount = 0;
wchar_t gRuntimeLogPath[MAX_PATH]{};
SRWLOCK gRuntimeLogLock = SRWLOCK_INIT;
SRWLOCK gDiscoveredDrawTextLock = SRWLOCK_INIT;
std::array<std::string, kMaximumDiscoveredDrawTexts> gDiscoveredDrawTexts{};
std::size_t gDiscoveredDrawTextCount = 0;

// 两条调用链都把 MultiByteToWideChar 的代码页设置为 65001，中文补丁因此使用 UTF-8。
constexpr std::array<std::uint8_t, 29> kFirstUtf8ConversionSignature{
    0xC7, 0x44, 0x24, 0x28, 0x00, 0x10, 0x00, 0x00, 0x44, 0x8B,
    0xCB, 0x48, 0x89, 0x44, 0x24, 0x20, 0x33, 0xD2, 0xB9, 0xE9,
    0xFD, 0x00, 0x00, 0xFF, 0x15, 0x09, 0xAD, 0x00, 0x00,
};
constexpr std::array<std::uint8_t, 40> kSecondUtf8ConversionSignature{
    0xC7, 0x44, 0x24, 0x28, 0x00, 0x10, 0x00, 0x00, 0x33, 0xD2,
    0x48, 0x89, 0x44, 0x24, 0x20, 0xB9, 0xE9, 0xFD, 0x00, 0x00,
    0x48, 0x89, 0xBC, 0x24, 0x58, 0x20, 0x00, 0x00, 0x41, 0xB9,
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x15, 0x3D, 0x62, 0x00, 0x00,
};

// 两条 RIP 相对指令分别引用 Segoe UI 与 Consolas；字节变化时拒绝改写字体槽。
constexpr std::array<std::uint8_t, 7> kRegularFontReferenceSignature{
    0x48, 0x8D, 0x0D, 0xC8, 0x63, 0x08, 0x00,
};
constexpr std::array<std::uint8_t, 7> kMonospaceFontReferenceSignature{
    0x48, 0x8D, 0x0D, 0xC3, 0x63, 0x08, 0x00,
};

// NeoEyes 的四处文字绘制调用统一进入该跳板，跳板再转到 GdipDrawString IAT。
constexpr std::array<std::uint8_t, 6> kGdipDrawStringThunkSignature{
    0xFF, 0x25, 0x42, 0x25, 0x00, 0x00,
};

// 四处可见文字绘制调用均直接进入上面的唯一跳板。
constexpr std::array<std::uintptr_t, 4> kGdipDrawStringCallRvas{
    0x7517, 0x7707, 0x793E, 0x79A1,
};
constexpr std::array<std::array<std::uint8_t, 5>, 4> kGdipDrawStringCallSignatures{{
    {{0xE8, 0x1C, 0x3A, 0x01, 0x00}},
    {{0xE8, 0x2C, 0x38, 0x01, 0x00}},
    {{0xE8, 0xF5, 0x35, 0x01, 0x00}},
    {{0xE8, 0x92, 0x35, 0x01, 0x00}},
}};

struct PeSectionView {
    std::uint8_t* address{};
    std::size_t size{};
};

struct WideFontReplacement {
    const wchar_t* original;
    const wchar_t* translation;
    std::size_t capacity;
};

// 原字体槽各有 11 个可写字符；使用系统自带微软雅黑以覆盖简体中文字形。
constexpr std::array<WideFontReplacement, 2> kFontReplacements{{
    {L"Segoe UI", L"微软雅黑", 11},
    {L"Consolas", L"微软雅黑", 11},
}};

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
    std::string line = "[NeoEyesCN] ";
    line.append(message);
    line.push_back('\n');
    OutputDebugStringA(line.c_str());

    if (gRuntimeLogPath[0] == L'\0') {
        return;
    }
    AcquireSRWLockExclusive(&gRuntimeLogLock);
    HANDLE file = CreateFileW(
        gRuntimeLogPath,
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
    ReleaseSRWLockExclusive(&gRuntimeLogLock);
}

void InitializeRuntimeLogPath() {
    std::array<wchar_t, MAX_PATH> processPath{};
    const DWORD length = GetModuleFileNameW(nullptr, processPath.data(), static_cast<DWORD>(processPath.size()));
    if (length == 0 || length >= processPath.size()) {
        return;
    }
    std::wstring path(processPath.data(), length);
    const auto separator = path.find_last_of(L"\\/");
    if (separator == std::wstring::npos) {
        return;
    }
    path.resize(separator + 1);
    path.append(L"NeoEyesCNv1.4.1.runtime.log");
    wcsncpy_s(gRuntimeLogPath, path.c_str(), _TRUNCATE);
    // 每次游戏启动生成独立采集结果，避免旧会话的名称干扰当前排查。
    DeleteFileW(gRuntimeLogPath);
}

void LogDiscoveredDrawText(std::string_view text) {
    if (text.empty()) {
        return;
    }
    bool isNew = false;
    AcquireSRWLockExclusive(&gDiscoveredDrawTextLock);
    const auto existing = std::find(
        gDiscoveredDrawTexts.begin(),
        gDiscoveredDrawTexts.begin() + static_cast<std::ptrdiff_t>(gDiscoveredDrawTextCount),
        text);
    if (existing == gDiscoveredDrawTexts.begin() + static_cast<std::ptrdiff_t>(gDiscoveredDrawTextCount) &&
        gDiscoveredDrawTextCount < kMaximumDiscoveredDrawTexts) {
        gDiscoveredDrawTexts[gDiscoveredDrawTextCount++] = std::string(text);
        isNew = true;
    }
    ReleaseSRWLockExclusive(&gDiscoveredDrawTextLock);
    if (isNew) {
        DebugLog("DISCOVER text=" + std::string(text));
    }
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

const IMAGE_NT_HEADERS64* ReadNtHeaders(HMODULE module) {
    if (module == nullptr) {
        return nullptr;
    }
    const auto* imageBase = reinterpret_cast<const std::uint8_t*>(module);
    const auto* dosHeader = reinterpret_cast<const IMAGE_DOS_HEADER*>(imageBase);
    if (dosHeader->e_magic != IMAGE_DOS_SIGNATURE) {
        return nullptr;
    }
    const auto* ntHeaders = reinterpret_cast<const IMAGE_NT_HEADERS64*>(imageBase + dosHeader->e_lfanew);
    if (ntHeaders->Signature != IMAGE_NT_SIGNATURE ||
        ntHeaders->FileHeader.Machine != IMAGE_FILE_MACHINE_AMD64 ||
        ntHeaders->OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR64_MAGIC) {
        return nullptr;
    }
    return ntHeaders;
}

bool ReadPeSection(HMODULE module, std::string_view sectionName, PeSectionView& output) {
    const auto* ntHeaders = ReadNtHeaders(module);
    if (ntHeaders == nullptr) {
        return false;
    }
    auto* imageBase = reinterpret_cast<std::uint8_t*>(module);
    const IMAGE_SECTION_HEADER* section = IMAGE_FIRST_SECTION(ntHeaders);
    for (WORD index = 0; index < ntHeaders->FileHeader.NumberOfSections; ++index, ++section) {
        const std::string_view currentName(
            reinterpret_cast<const char*>(section->Name),
            strnlen_s(reinterpret_cast<const char*>(section->Name), IMAGE_SIZEOF_SHORT_NAME));
        if (currentName == sectionName) {
            output.address = imageBase + section->VirtualAddress;
            output.size = section->Misc.VirtualSize;
            return output.size > 0;
        }
    }
    return false;
}

bool ContainsNullTerminatedString(const PeSectionView& section, std::string_view expected) {
    if (expected.empty() || section.size <= expected.size()) {
        return false;
    }
    const auto* end = section.address + section.size - expected.size() - 1;
    for (const auto* cursor = section.address; cursor <= end; ++cursor) {
        if (std::memcmp(cursor, expected.data(), expected.size()) == 0 && cursor[expected.size()] == 0) {
            return true;
        }
    }
    return false;
}

template <std::size_t Size>
bool MatchesCodeSignature(HMODULE module, std::uintptr_t rva, const std::array<std::uint8_t, Size>& expected) {
    const auto* target = reinterpret_cast<const std::uint8_t*>(module) + rva;
    return std::equal(expected.begin(), expected.end(), target);
}

bool ValidateTarget(HMODULE module) {
    const auto* ntHeaders = ReadNtHeaders(module);
    if (ntHeaders == nullptr || ntHeaders->FileHeader.TimeDateStamp != kTargetPeTimestamp ||
        ntHeaders->OptionalHeader.SizeOfImage != kTargetSizeOfImage) {
        DebugLog("NeoEyes PE 标识不匹配，已停用汉化。");
        return false;
    }
    PeSectionView readOnlyData{};
    if (!ReadPeSection(module, ".rdata", readOnlyData) ||
        readOnlyData.size != 0x7B1F4 ||
        !ContainsNullTerminatedString(readOnlyData, "Search by name") ||
        !ContainsNullTerminatedString(readOnlyData, "NEO EYES / NPC  [%d/%d]   8/2 move  5 enter  0 back  7 close")) {
        DebugLog("NeoEyes 界面标识不匹配，已停用汉化。");
        return false;
    }
    if (!MatchesCodeSignature(module, kFirstUtf8ConversionRva, kFirstUtf8ConversionSignature) ||
        !MatchesCodeSignature(module, kSecondUtf8ConversionRva, kSecondUtf8ConversionSignature) ||
        !MatchesCodeSignature(module, kRegularFontReferenceRva, kRegularFontReferenceSignature) ||
        !MatchesCodeSignature(module, kMonospaceFontReferenceRva, kMonospaceFontReferenceSignature) ||
        !MatchesCodeSignature(module, kGdipDrawStringThunkRva, kGdipDrawStringThunkSignature)) {
        DebugLog("NeoEyes UTF-8 或字体代码特征不匹配，已停用汉化。");
        return false;
    }
    for (std::size_t index = 0; index < kGdipDrawStringCallRvas.size(); ++index) {
        if (!MatchesCodeSignature(module, kGdipDrawStringCallRvas[index],
                                  kGdipDrawStringCallSignatures[index])) {
            DebugLog("NeoEyes GdipDrawString 可见调用点不匹配，已停用汉化。");
            return false;
        }
    }
    return true;
}

bool WriteMemory(void* destination, const void* source, std::size_t size) {
    DWORD oldProtection = 0;
    if (!VirtualProtect(destination, size, PAGE_READWRITE, &oldProtection)) {
        return false;
    }
    std::memcpy(destination, source, size);
    FlushInstructionCache(GetCurrentProcess(), destination, size);
    DWORD ignoredProtection = 0;
    VirtualProtect(destination, size, oldProtection, &ignoredProtection);
    return true;
}

bool EqualsAsciiInsensitive(std::string_view left, std::string_view right) {
    if (left.size() != right.size()) {
        return false;
    }
    return std::equal(left.begin(), left.end(), right.begin(), [](char a, char b) {
        const auto lower = [](char value) {
            return value >= 'A' && value <= 'Z' ? static_cast<char>(value + ('a' - 'A')) : value;
        };
        return lower(a) == lower(b);
    });
}

const generated::CatalogName* FindOfficialCatalogName(std::string_view source) {
    const auto* begin = generated::kCatalogNames;
    const auto* end = begin + generated::kCatalogNameCount;
    const auto* match = std::lower_bound(
        begin,
        end,
        source,
        [](const generated::CatalogName& entry, std::string_view value) {
            return std::string_view(entry.source) < value;
        });
    if (match == end || std::string_view(match->source) != source) {
        return nullptr;
    }
    return match;
}

std::string FormatOfficialCatalogName(std::string_view source, std::string_view translation) {
    std::string result(translation);
    const std::size_t separator = source.find_last_of('_');
    const auto isDigit = [](char value) { return value >= '0' && value <= '9'; };
    if (separator != std::string_view::npos && separator + 1 < source.size() &&
        std::all_of(source.begin() + static_cast<std::ptrdiff_t>(separator + 1), source.end(), isDigit)) {
        result.append(" #");
        result.append(source.substr(separator + 1));
    }
    return result;
}

const generated::CatalogTerm* FindCatalogTerm(std::string_view source) {
    for (const auto& entry : generated::kCatalogTerms) {
        if (EqualsAsciiInsensitive(source, entry.source)) {
            return &entry;
        }
    }
    return nullptr;
}

bool IsAsciiUpper(char value) {
    return value >= 'A' && value <= 'Z';
}

bool IsAsciiLower(char value) {
    return value >= 'a' && value <= 'z';
}

bool IsAsciiDigit(char value) {
    return value >= '0' && value <= '9';
}

bool IsCatalogIdentifierCharacter(char value) {
    return IsAsciiUpper(value) || IsAsciiLower(value) || IsAsciiDigit(value) || value == '_';
}

std::vector<std::string_view> SplitCamelCase(std::string_view chunk) {
    std::vector<std::string_view> words;
    if (chunk.empty()) {
        return words;
    }
    std::size_t start = 0;
    for (std::size_t index = 1; index < chunk.size(); ++index) {
        const char previous = chunk[index - 1];
        const char current = chunk[index];
        const bool letterToDigit = !IsAsciiDigit(previous) && IsAsciiDigit(current);
        const bool digitToLetter = IsAsciiDigit(previous) && !IsAsciiDigit(current);
        const bool lowerToUpper = IsAsciiLower(previous) && IsAsciiUpper(current);
        const bool acronymToWord = IsAsciiUpper(previous) && IsAsciiUpper(current) &&
            index + 1 < chunk.size() && IsAsciiLower(chunk[index + 1]);
        if (letterToDigit || digitToLetter || lowerToUpper || acronymToWord) {
            words.push_back(chunk.substr(start, index - start));
            start = index;
        }
    }
    words.push_back(chunk.substr(start));
    return words;
}

void AppendCatalogCore(std::string& output, std::string_view value) {
    if (value.empty()) {
        return;
    }
    if (!output.empty() && static_cast<unsigned char>(output.back()) < 0x80 &&
        static_cast<unsigned char>(value.front()) < 0x80) {
        output.push_back(' ');
    }
    output.append(value);
}

void AppendCatalogModifier(std::string& output, std::string_view value) {
    if (!output.empty()) {
        output.append("·");
    }
    output.append(value);
}

struct CatalogNameParts {
    std::string category;
    std::string core;
    std::string modifiers;
    std::string numericSuffix;
    bool translated{};
};

std::string FormatCatalogNameParts(const CatalogNameParts& parts) {
    std::string result = parts.category;
    if (!result.empty() && !parts.core.empty()) {
        result.append("·");
    }
    result.append(parts.core);
    if (!parts.modifiers.empty()) {
        if (parts.core.empty() && parts.category.empty()) {
            result.append(parts.modifiers);
        } else {
            result.append("（");
            result.append(parts.modifiers);
            result.append("）");
        }
    }
    if (!parts.numericSuffix.empty()) {
        result.append(" #");
        result.append(parts.numericSuffix);
    }
    return result;
}

void ApplyCatalogTerm(CatalogNameParts& parts, std::string_view source, const generated::CatalogTerm* term) {
    if (term == nullptr) {
        AppendCatalogCore(parts.core, source);
        return;
    }
    switch (term->kind) {
        case generated::CatalogTermKind::Ignore:
            return;
        case generated::CatalogTermKind::Category:
            parts.translated = true;
            AppendCatalogCore(parts.category, term->translation);
            return;
        case generated::CatalogTermKind::Core:
            parts.translated = true;
            AppendCatalogCore(parts.core, term->translation);
            return;
        case generated::CatalogTermKind::Modifier:
            parts.translated = true;
            AppendCatalogModifier(parts.modifiers, term->translation);
            return;
    }
}

void TranslateCatalogChunk(CatalogNameParts& parts, std::string_view chunk) {
    if (chunk.empty()) {
        return;
    }
    if (std::all_of(chunk.begin(), chunk.end(), IsAsciiDigit)) {
        if (!parts.numericSuffix.empty()) {
            parts.numericSuffix.push_back('.');
        }
        parts.numericSuffix.append(chunk);
        return;
    }
    if (const auto* wholeTerm = FindCatalogTerm(chunk)) {
        ApplyCatalogTerm(parts, chunk, wholeTerm);
        return;
    }
    const auto words = SplitCamelCase(chunk);
    if (words.size() == 1) {
        AppendCatalogCore(parts.core, chunk);
        return;
    }
    for (const auto word : words) {
        ApplyCatalogTerm(parts, word, FindCatalogTerm(word));
    }
}

std::optional<std::string> TranslateCatalogIdentifier(std::string_view identifier) {
    if (const auto* official = FindOfficialCatalogName(identifier)) {
        return FormatOfficialCatalogName(identifier, official->translation);
    }
    if (identifier.size() < 5 || identifier.find('_') == std::string_view::npos) {
        return std::nullopt;
    }
    CatalogNameParts parts;
    std::size_t start = 0;
    while (start <= identifier.size()) {
        const std::size_t separator = identifier.find('_', start);
        const std::size_t end = separator == std::string_view::npos ? identifier.size() : separator;
        TranslateCatalogChunk(parts, identifier.substr(start, end - start));
        if (separator == std::string_view::npos) {
            break;
        }
        start = separator + 1;
    }
    if (!parts.translated || parts.core.empty()) {
        return std::nullopt;
    }
    return FormatCatalogNameParts(parts);
}

std::optional<std::string> TranslateCatalogDisplayToken(std::string_view token) {
    if (const auto* official = FindOfficialCatalogName(token)) {
        return FormatOfficialCatalogName(token, official->translation);
    }
    if (const auto* wholeTerm = FindCatalogTerm(token)) {
        CatalogNameParts parts;
        ApplyCatalogTerm(parts, token, wholeTerm);
        if (parts.translated) {
            return FormatCatalogNameParts(parts);
        }
        return std::nullopt;
    }

    const auto words = SplitCamelCase(token);
    if (words.size() < 2) {
        return std::nullopt;
    }
    CatalogNameParts parts;
    for (const auto word : words) {
        const auto* term = FindCatalogTerm(word);
        if (term == nullptr) {
            return std::nullopt;
        }
        ApplyCatalogTerm(parts, word, term);
    }
    if (!parts.translated) {
        return std::nullopt;
    }
    return FormatCatalogNameParts(parts);
}

std::optional<std::string> TranslateCatalogDisplayText(std::string_view source) {
    std::string translated;
    translated.reserve(source.size() + 32);
    bool changed = false;
    std::size_t cursor = 0;
    while (cursor < source.size()) {
        // 目录 ID 本身由下划线连接多个片段；必须把完整 ID 一次交给词典，不能先拆掉下划线。
        if (!IsCatalogIdentifierCharacter(source[cursor])) {
            translated.push_back(source[cursor++]);
            continue;
        }
        const std::size_t start = cursor;
        while (cursor < source.size() && IsCatalogIdentifierCharacter(source[cursor])) {
            ++cursor;
        }
        const std::string_view candidate = source.substr(start, cursor - start);
        std::optional<std::string> catalogName;
        if (candidate.find('_') != std::string_view::npos) {
            catalogName = TranslateCatalogIdentifier(candidate);
        } else {
            catalogName = TranslateCatalogDisplayToken(candidate);
        }
        if (catalogName) {
            translated.append(*catalogName);
            changed = true;
        } else {
            translated.append(candidate);
        }
    }
    return changed ? std::optional<std::string>(std::move(translated)) : std::nullopt;
}

std::optional<std::string> ConvertWideTextToUtf8(const wchar_t* source, int sourceLength) {
    if (source == nullptr || sourceLength == 0) {
        return std::nullopt;
    }
    const std::size_t terminatedLength = wcsnlen_s(source, kMaximumRenderedTextBytes);
    if (terminatedLength == kMaximumRenderedTextBytes) {
        return std::nullopt;
    }
    // GDI+ 的部分调用会把容量/格式长度传进来，而不是实际可见字符数；优先采用 NUL 结尾。
    const std::size_t characterCount = sourceLength < 0
        ? terminatedLength
        : std::min<std::size_t>(static_cast<std::size_t>(sourceLength), terminatedLength);
    if (characterCount == 0 || characterCount > kMaximumRenderedTextBytes) {
        return std::nullopt;
    }
    const int requiredBytes = WideCharToMultiByte(
        CP_UTF8, 0, source, static_cast<int>(characterCount), nullptr, 0, nullptr, nullptr);
    if (requiredBytes <= 0) {
        return std::nullopt;
    }
    std::string utf8(static_cast<std::size_t>(requiredBytes), '\0');
    if (WideCharToMultiByte(
            CP_UTF8,
            0,
            source,
            static_cast<int>(characterCount),
            utf8.data(),
            requiredBytes,
            nullptr,
            nullptr) != requiredBytes) {
        return std::nullopt;
    }
    return utf8;
}

std::optional<std::wstring> ConvertUtf8TextToWide(std::string_view source) {
    if (source.empty()) {
        return std::nullopt;
    }
    const int requiredCharacters = MultiByteToWideChar(
        CP_UTF8, MB_ERR_INVALID_CHARS, source.data(), static_cast<int>(source.size()), nullptr, 0);
    if (requiredCharacters <= 0) {
        return std::nullopt;
    }
    std::wstring wide(static_cast<std::size_t>(requiredCharacters), L'\0');
    if (MultiByteToWideChar(
            CP_UTF8,
            MB_ERR_INVALID_CHARS,
            source.data(),
            static_cast<int>(source.size()),
            wide.data(),
            requiredCharacters) != requiredCharacters) {
        return std::nullopt;
    }
    return wide;
}

int WINAPI LocalizedGdipDrawString(
    void* graphics,
    const wchar_t* source,
    int sourceLength,
    const void* font,
    const void* layoutRectangle,
    const void* stringFormat,
    const void* brush) {
    if (gOriginalGdipDrawString == nullptr) {
        return 1;
    }
    const auto utf8 = ConvertWideTextToUtf8(source, sourceLength);
    const LONG diagnosticIndex = InterlockedIncrement(&gDrawDiagnosticCount);
    if (diagnosticIndex <= kMaximumDrawDiagnostics) {
        DebugLog("DRAW #" + std::to_string(diagnosticIndex) +
            " len=" + std::to_string(sourceLength) +
            " text=" + (utf8 ? *utf8 : std::string("<wide-to-utf8 failed>")));
        if (!utf8 && source != nullptr) {
            std::string units = "wide units=";
            const std::size_t unitCount = sourceLength > 0
                ? std::min<std::size_t>(static_cast<std::size_t>(sourceLength), 8)
                : 8;
            for (std::size_t index = 0; index < unitCount; ++index) {
                if (index != 0) {
                    units.push_back(',');
                }
                units.append(std::to_string(static_cast<unsigned int>(source[index])));
            }
            DebugLog(units);
        }
    }
    if (!utf8) {
        return gOriginalGdipDrawString(
            graphics, source, sourceLength, font, layoutRectangle, stringFormat, brush);
    }
    LogDiscoveredDrawText(*utf8);
    const auto translated = TranslateCatalogDisplayText(*utf8);
    if (!translated) {
        return gOriginalGdipDrawString(
            graphics, source, sourceLength, font, layoutRectangle, stringFormat, brush);
    }
    const auto translatedWide = ConvertUtf8TextToWide(*translated);
    if (!translatedWide) {
        return gOriginalGdipDrawString(
            graphics, source, sourceLength, font, layoutRectangle, stringFormat, brush);
    }
    if (diagnosticIndex <= kMaximumDrawDiagnostics) {
        DebugLog("DRAW translated=" + *translated);
    }
    return gOriginalGdipDrawString(
        graphics,
        translatedWide->c_str(),
        static_cast<int>(translatedWide->size()),
        font,
        layoutRectangle,
        stringFormat,
        brush);
}

bool InstallGdipDrawStringHook(HMODULE module) {
    const auto* ntHeaders = ReadNtHeaders(module);
    if (ntHeaders == nullptr) {
        return false;
    }
    auto* imageBase = reinterpret_cast<std::uint8_t*>(module);
    // 目标样本已在 ValidateTarget 中锁定时间戳、镜像大小、跳板字节和 GdipDrawString
    // 导入布局。直接写唯一 IAT 槽，兼容会清理 OriginalFirstThunk 的 ASI 加载器。
    auto* importSlot = reinterpret_cast<ULONGLONG*>(imageBase + kGdipDrawStringImportSlotRva);
    gGdipDrawStringImportSlot = importSlot;
    DebugLog("IAT slot=" + std::to_string(reinterpret_cast<std::uintptr_t>(importSlot)) +
        " before=" + std::to_string(static_cast<std::uintptr_t>(*importSlot)));
    const auto hook = reinterpret_cast<ULONGLONG>(&LocalizedGdipDrawString);
    if (*importSlot == hook) {
        return gOriginalGdipDrawString != nullptr;
    }
    gOriginalGdipDrawString = reinterpret_cast<GdipDrawStringFunction>(*importSlot);
    if (gOriginalGdipDrawString == nullptr || !WriteMemory(importSlot, &hook, sizeof(hook))) {
        gOriginalGdipDrawString = nullptr;
        return false;
    }
    gGdipDrawStringThunkAddress = imageBase + kGdipDrawStringThunkRva;
    if (std::memcmp(gGdipDrawStringThunkAddress, kGdipDrawStringThunkSignature.data(),
                    kGdipDrawStringThunkSignature.size()) == 0) {
        constexpr std::size_t kRelaySize = 0x1000;
        if (gGdipDrawStringRelay == nullptr) {
            const auto targetAddress = reinterpret_cast<std::uintptr_t>(gGdipDrawStringThunkAddress);
            constexpr std::uintptr_t kAllocationStep = 0x100000;
            constexpr std::uintptr_t kMaximumDistance = 0x7FFF0000;
            for (std::uintptr_t distance = 0; distance <= kMaximumDistance && gGdipDrawStringRelay == nullptr;
                 distance += kAllocationStep) {
                const std::array<std::uintptr_t, 2> candidates{
                    targetAddress + distance,
                    targetAddress > distance ? targetAddress - distance : 0,
                };
                for (const auto candidate : candidates) {
                    if (candidate == 0) {
                        continue;
                    }
                    gGdipDrawStringRelay = static_cast<std::uint8_t*>(VirtualAlloc(
                        reinterpret_cast<void*>(candidate),
                        kRelaySize,
                        MEM_RESERVE | MEM_COMMIT,
                        PAGE_EXECUTE_READWRITE));
                    if (gGdipDrawStringRelay != nullptr) {
                        break;
                    }
                }
            }
        }
        if (gGdipDrawStringRelay != nullptr) {
            std::array<std::uint8_t, 12> relayCode{
                0x48, 0xB8,
                0, 0, 0, 0, 0, 0, 0, 0,
                0xFF, 0xE0,
            };
            const auto hookAddress = reinterpret_cast<ULONGLONG>(&LocalizedGdipDrawString);
            std::memcpy(relayCode.data() + 2, &hookAddress, sizeof(hookAddress));
            WriteMemory(gGdipDrawStringRelay, relayCode.data(), relayCode.size());
            const auto nextInstruction = reinterpret_cast<std::uintptr_t>(gGdipDrawStringThunkAddress + 5);
            const auto relayAddress = reinterpret_cast<std::uintptr_t>(gGdipDrawStringRelay);
            const auto relativeDistance = static_cast<std::int64_t>(relayAddress) -
                static_cast<std::int64_t>(nextInstruction);
            if (relativeDistance >= INT32_MIN && relativeDistance <= INT32_MAX) {
                std::array<std::uint8_t, 6> jumpPatch{0xE9, 0, 0, 0, 0, 0x90};
                const auto relative = static_cast<std::int32_t>(relativeDistance);
                std::memcpy(jumpPatch.data() + 1, &relative, sizeof(relative));
                WriteMemory(gGdipDrawStringThunkAddress, jumpPatch.data(), jumpPatch.size());
                DebugLog("relay=" + std::to_string(reinterpret_cast<std::uintptr_t>(gGdipDrawStringRelay)) +
                    " hook=" + std::to_string(reinterpret_cast<std::uintptr_t>(&LocalizedGdipDrawString)));
            }
        }
    }
    return *importSlot == hook ||
        (gGdipDrawStringThunkAddress != nullptr && gGdipDrawStringRelay != nullptr);
}

// 部分 ASI 加载器或保护层会在模块初始化后重写 IAT；持续校验只针对已确认的
// GdipDrawString 槽，不修改游戏其他导入，也不改变原始召唤逻辑。
DWORD WINAPI GdipDrawStringHookWatchdog(void* context) {
    const auto targetModule = static_cast<HMODULE>(context);
    const auto hook = reinterpret_cast<ULONGLONG>(&LocalizedGdipDrawString);
    while (GetModuleHandleW(kTargetModuleName) == targetModule) {
        Sleep(kHookWatchdogPollMilliseconds);
        if (gGdipDrawStringImportSlot == nullptr || gOriginalGdipDrawString == nullptr) {
            continue;
        }
        if (*gGdipDrawStringImportSlot != hook) {
            WriteMemory(gGdipDrawStringImportSlot, &hook, sizeof(hook));
        }
        if (gGdipDrawStringThunkAddress != nullptr && gGdipDrawStringRelay != nullptr) {
            const auto nextInstruction = reinterpret_cast<std::uintptr_t>(gGdipDrawStringThunkAddress + 5);
            const auto relayAddress = reinterpret_cast<std::uintptr_t>(gGdipDrawStringRelay);
            const auto relativeDistance = static_cast<std::int64_t>(relayAddress) -
                static_cast<std::int64_t>(nextInstruction);
            if (relativeDistance >= INT32_MIN && relativeDistance <= INT32_MAX) {
                std::array<std::uint8_t, 6> jumpPatch{0xE9, 0, 0, 0, 0, 0x90};
                const auto relative = static_cast<std::int32_t>(relativeDistance);
                std::memcpy(jumpPatch.data() + 1, &relative, sizeof(relative));
                if (std::memcmp(gGdipDrawStringThunkAddress, jumpPatch.data(), jumpPatch.size()) != 0) {
                    WriteMemory(gGdipDrawStringThunkAddress, jumpPatch.data(), jumpPatch.size());
                }
            }
        }
    }
    return 0;
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

std::size_t PatchWideFontEntry(PeSectionView section, const WideFontReplacement& entry) {
    const std::size_t originalLength = std::wcslen(entry.original);
    const std::size_t translatedLength = std::wcslen(entry.translation);
    const std::size_t slotBytes = (entry.capacity + 1) * sizeof(wchar_t);
    if (originalLength == 0 || entry.capacity < originalLength || translatedLength > entry.capacity ||
        section.size < slotBytes) {
        return 0;
    }
    std::size_t patchedCount = 0;
    for (std::size_t offset = 0; offset + slotBytes <= section.size; offset += alignof(wchar_t)) {
        auto* cursor = section.address + offset;
        if (std::memcmp(cursor, entry.original, (originalLength + 1) * sizeof(wchar_t)) != 0) {
            continue;
        }
        const bool paddingIsEmpty = std::all_of(
            cursor + originalLength * sizeof(wchar_t),
            cursor + slotBytes,
            [](std::uint8_t value) { return value == 0; });
        if (!paddingIsEmpty) {
            continue;
        }
        std::array<wchar_t, 12> replacement{};
        std::copy_n(entry.translation, translatedLength, replacement.begin());
        if (!WriteMemory(cursor, replacement.data(), slotBytes)) {
            return patchedCount;
        }
        ++patchedCount;
        offset += slotBytes - alignof(wchar_t);
    }
    return patchedCount;
}

DWORD WINAPI InitializeLocalization(void*) {
    InitializeRuntimeLogPath();
    DebugLog("初始化 v1.4.1");
    HMODULE targetModule = WaitForTargetModule();
    if (targetModule == nullptr) {
        DebugLog("等待 NeoEyesSimpleMenu.asi 超时，未执行任何修改。");
        return 0;
    }
    if (!ValidateTarget(targetModule)) {
        return 0;
    }
    PeSectionView readOnlyData{};
    if (!ReadPeSection(targetModule, ".rdata", readOnlyData)) {
        return 0;
    }
    std::size_t patchedFonts = 0;
    for (const auto& entry : kFontReplacements) {
        patchedFonts += PatchWideFontEntry(readOnlyData, entry);
    }
    if (patchedFonts != kFontReplacements.size()) {
        DebugLog("NeoEyes 字体槽数量不匹配，未执行文本替换。");
        return 0;
    }
    std::size_t patchedTexts = 0;
    std::size_t missingTexts = 0;
    for (const auto& entry : generated::kTranslations) {
        const std::size_t patchedCount = PatchTranslationEntry(readOnlyData, entry);
        patchedTexts += patchedCount;
        if (patchedCount != entry.occurrences) {
            ++missingTexts;
            DebugLog(
                "文本槽未匹配：" + std::string(entry.original) +
                " expected=" + std::to_string(entry.occurrences) +
                " actual=" + std::to_string(patchedCount));
        }
    }
    if (patchedTexts != generated::kExpectedPatchCount) {
        DebugLog(
            "部分 NeoEyes 文本槽未匹配，继续安装目录 Hook。missing=" +
            std::to_string(missingTexts) +
            " matched=" + std::to_string(patchedTexts) +
            " expected=" + std::to_string(generated::kExpectedPatchCount));
    }
    // 目录宽字符串可能在目标 ASI 的初始化阶段就被缓存，等初始化完成后再接管最终绘制。
    Sleep(kHookWarmupMilliseconds);
    if (!InstallGdipDrawStringHook(targetModule)) {
        DebugLog("NeoEyes GdipDrawString 目录显示翻译 Hook 安装失败。");
        return 0;
    }
    if (InterlockedCompareExchange(&gHookWatchdogStarted, 1, 0) == 0) {
        if (HANDLE watchdog = CreateThread(nullptr, 0, GdipDrawStringHookWatchdog, targetModule, 0, nullptr)) {
            CloseHandle(watchdog);
        }
    }
    DebugLog("NeoEyes 运行时中文映射与最终绘制目录翻译已启用。");
    return 0;
}

}  // namespace neoeyes_cn

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID) {
    if (reason != DLL_PROCESS_ATTACH) {
        return TRUE;
    }
    DisableThreadLibraryCalls(module);
    if (!neoeyes_cn::IsTargetProcess()) {
        return TRUE;
    }
    HANDLE thread = CreateThread(nullptr, 0, neoeyes_cn::InitializeLocalization, nullptr, 0, nullptr);
    if (thread != nullptr) {
        CloseHandle(thread);
    }
    return TRUE;
}
