cmake_minimum_required(VERSION 3.24)

project(NeoEyesCN VERSION 1.3.0 LANGUAGES CXX)

add_library(NeoEyesCN SHARED
    src/neoeyes_localizer.cpp
    src/version.rc
)

target_compile_features(NeoEyesCN PRIVATE cxx_std_20)
target_compile_definitions(NeoEyesCN PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX UNICODE _UNICODE)
target_compile_options(NeoEyesCN PRIVATE /utf-8 /W4 /permissive- /EHsc /guard:cf-)
target_link_options(NeoEyesCN PRIVATE /INCREMENTAL:NO /OPT:REF /OPT:ICF)

set_target_properties(NeoEyesCN PROPERTIES
    PREFIX ""
    SUFFIX ".asi"
    OUTPUT_NAME "NeoEyesCN"
    MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>"
)

add_executable(NeoEyesCatalogTranslationTest
    tests/catalog_translation_test.cpp
)
target_compile_features(NeoEyesCatalogTranslationTest PRIVATE cxx_std_20)
target_compile_definitions(NeoEyesCatalogTranslationTest PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX UNICODE _UNICODE)
target_compile_options(NeoEyesCatalogTranslationTest PRIVATE /utf-8 /W4 /permissive- /EHsc)
set_target_properties(NeoEyesCatalogTranslationTest PROPERTIES
    MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>"
)
