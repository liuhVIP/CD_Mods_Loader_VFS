// NeoEyes 召唤目录显示翻译回归：直接复用伴生 ASI 实现验证关键名称和误匹配边界。
#include <cstdlib>
#include <iostream>
#include <string>
#include <string_view>

#include "../src/neoeyes_localizer.cpp"

namespace {

std::wstring gCapturedDrawText;

int WINAPI CaptureGdipDrawString(
    void*,
    const wchar_t* source,
    int sourceLength,
    const void*,
    const void*,
    const void*,
    const void*) {
    const std::size_t length = sourceLength < 0
        ? std::wcslen(source)
        : static_cast<std::size_t>(sourceLength);
    gCapturedDrawText.assign(source, length);
    return 0;
}

void RequireTranslation(std::string_view source, std::string_view expected) {
    const auto actual = neoeyes_cn::TranslateCatalogIdentifier(source);
    if (!actual || *actual != expected) {
        std::cerr << "翻译不匹配：" << source << "\n";
        std::exit(EXIT_FAILURE);
    }
}

}  // namespace

int main() {
    RequireTranslation("Animal_AlpineIbex_10", "动物·高山野山羊 #10");
    RequireTranslation("Animal_Arctic_Fox_Wild_30038", "动物·北极狐狸（野生） #30038");
    RequireTranslation("Animal_Axolotl_Wild_32430", "动物·美西螈（野生） #32430");
    RequireTranslation("Animal_Desert_Fox_Wild_31410", "动物·沙漠狐狸（野生） #31410");
    RequireTranslation("Animal_Fox_Wild_30046", "动物·狐狸（野生） #30046");

    const auto rendered = neoeyes_cn::TranslateCatalogDisplayText("01  Animal_AlpineIbex_10");
    if (!rendered || *rendered != "01  动物·高山野山羊 #10") {
        std::cerr << "列表行翻译不匹配。\n";
        return EXIT_FAILURE;
    }
    const auto multiRendered = neoeyes_cn::TranslateCatalogDisplayText(
        "02  Animal_Desert_Fox_Wild_31410  Animal_Arctic_Fox_Wild_30038");
    if (!multiRendered ||
        *multiRendered != "02  动物·沙漠狐狸（野生） #31410  动物·北极狐狸（野生） #30038") {
        std::cerr << "同一绘制文本中的多目录翻译不匹配。\n";
        return EXIT_FAILURE;
    }
    const auto categoryRendered = neoeyes_cn::TranslateCatalogDisplayText(
        "01  Citizen        (766)  02  MiddleCitizen        (135)  03  Stallholder        (12)");
    if (!categoryRendered ||
        *categoryRendered != "01  市民        (766)  02  中型市民        (135)  03  摊主        (12)") {
        std::cerr << "多层分类菜单翻译不匹配。\n";
        return EXIT_FAILURE;
    }
    const auto screenshotRendered = neoeyes_cn::TranslateCatalogDisplayText(
        "01 Citizen 02 Unique 03 MiddleCitizen 04 Demeniss 05 Caliburn 06 Slave "
        "07 Bandit 08 Wells 09 Noble 10 Wandering 11 Drunk 12 Stallholder");
    if (!screenshotRendered ||
        *screenshotRendered !=
            "01 市民 02 独特 03 中型市民 04 德梅尼斯 05 卡利本 06 奴隶 "
            "07 强盗 08 威尔斯 09 贵族 10 流浪者 11 醉汉 12 摊主") {
        std::cerr << "截图分类名称翻译不匹配。\n";
        return EXIT_FAILURE;
    }
    const auto animalRendered = neoeyes_cn::TranslateCatalogDisplayText(
        "02 BananaButterfly 03 Banded_Peacock 04 Bee 05 Beetle 06 BlackMantis "
        "07 BlueCopper 08 BluePansyButterfly 09 Bumblebee 10 CamelCricket");
    if (!animalRendered ||
        *animalRendered !=
            "02 香蕉蝴蝶 03 条纹孔雀 04 蜜蜂 05 甲虫 06 黑色螳螂 "
            "07 蓝色铜色 08 蓝色三色堇蝴蝶 09 熊蜂 10 骆驼蟋蟀") {
        std::cerr << "动物分类名称翻译不匹配。\n";
        return EXIT_FAILURE;
    }
    if (neoeyes_cn::TranslateCatalogIdentifier("Internal_Debug_Value")) {
        std::cerr << "未知内部标识不应被翻译。\n";
        return EXIT_FAILURE;
    }

    neoeyes_cn::gOriginalGdipDrawString = &CaptureGdipDrawString;
    neoeyes_cn::LocalizedGdipDrawString(
        nullptr,
        L"01  Animal_Desert_Fox_Wild_31410",
        -1,
        nullptr,
        nullptr,
        nullptr,
        nullptr);
    if (gCapturedDrawText != L"01  动物·沙漠狐狸（野生） #31410") {
        std::cerr << "GdipDrawString 最终绘制翻译不匹配。\n";
        return EXIT_FAILURE;
    }
    neoeyes_cn::LocalizedGdipDrawString(
        nullptr,
        L"03  Banded_Peacock",
        18,
        nullptr,
        nullptr,
        nullptr,
        nullptr);
    if (gCapturedDrawText != L"03  条纹孔雀") {
        std::cerr << "正数 sourceLength 绘制回归失败。\n";
        return EXIT_FAILURE;
    }

    constexpr std::size_t fakeImageSize = 0x14000;
    auto* fakeImage = static_cast<std::uint8_t*>(VirtualAlloc(
        nullptr, fakeImageSize, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE));
    if (fakeImage == nullptr) {
        std::cerr << "无法分配模拟 PE。\n";
        return EXIT_FAILURE;
    }
    auto* dosHeader = reinterpret_cast<IMAGE_DOS_HEADER*>(fakeImage);
    dosHeader->e_magic = IMAGE_DOS_SIGNATURE;
    dosHeader->e_lfanew = 0x100;
    auto* ntHeaders = reinterpret_cast<IMAGE_NT_HEADERS64*>(fakeImage + dosHeader->e_lfanew);
    ntHeaders->Signature = IMAGE_NT_SIGNATURE;
    ntHeaders->FileHeader.Machine = IMAGE_FILE_MACHINE_AMD64;
    ntHeaders->OptionalHeader.Magic = IMAGE_NT_OPTIONAL_HDR64_MAGIC;
    ntHeaders->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT].VirtualAddress = 0x1000;
    ntHeaders->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT].Size =
        sizeof(IMAGE_IMPORT_DESCRIPTOR) * 2;
    auto* importDescriptor = reinterpret_cast<IMAGE_IMPORT_DESCRIPTOR*>(fakeImage + 0x1000);
    importDescriptor->OriginalFirstThunk = 0x1100;
    importDescriptor->FirstThunk = 0x13400;
    importDescriptor->Name = 0x1300;
    auto* originalThunk = reinterpret_cast<IMAGE_THUNK_DATA64*>(fakeImage + 0x1100);
    originalThunk[0].u1.AddressOfData = 0x1200;
    auto* importByName = reinterpret_cast<IMAGE_IMPORT_BY_NAME*>(fakeImage + 0x1200);
    strcpy_s(reinterpret_cast<char*>(importByName->Name), 32, "GdipDrawString");
    strcpy_s(reinterpret_cast<char*>(fakeImage + 0x1300), 32, "gdiplus.dll");
    auto* importSlot = reinterpret_cast<ULONGLONG*>(fakeImage + 0x13400);
    *importSlot = reinterpret_cast<ULONGLONG>(&CaptureGdipDrawString);
    neoeyes_cn::gOriginalGdipDrawString = nullptr;
    if (!neoeyes_cn::InstallGdipDrawStringHook(reinterpret_cast<HMODULE>(fakeImage)) ||
        *importSlot != reinterpret_cast<ULONGLONG>(&neoeyes_cn::LocalizedGdipDrawString) ||
        neoeyes_cn::gOriginalGdipDrawString != &CaptureGdipDrawString) {
        VirtualFree(fakeImage, 0, MEM_RELEASE);
        std::cerr << "GdipDrawString IAT 安装回归失败。\n";
        return EXIT_FAILURE;
    }
    VirtualFree(fakeImage, 0, MEM_RELEASE);
    return EXIT_SUCCESS;
}
