cmake_minimum_required(VERSION 3.24)

project(TrinityCN VERSION 0.7.3 LANGUAGES CXX)

add_library(TrinityCN SHARED
    src/trinity_localizer.cpp
    src/version.rc
)

target_compile_features(TrinityCN PRIVATE cxx_std_20)
target_compile_definitions(TrinityCN PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX UNICODE _UNICODE)
target_compile_options(TrinityCN PRIVATE /utf-8 /W4 /permissive- /EHsc /guard:cf-)
target_link_options(TrinityCN PRIVATE /INCREMENTAL:NO /OPT:REF /OPT:ICF)

set_target_properties(TrinityCN PROPERTIES
    PREFIX ""
    SUFFIX ".asi"
    OUTPUT_NAME "TrinityCN"
    MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>"
)
