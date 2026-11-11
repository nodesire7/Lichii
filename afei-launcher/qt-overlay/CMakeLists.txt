# SPDX-License-Identifier: GPL-3.0-or-later
cmake_minimum_required(VERSION 3.21)
project(AfeiTranslation LANGUAGES CXX)

find_package(Qt6 6.8.3 EXACT REQUIRED COMPONENTS Widgets)

set(CMAKE_AUTOMOC ON)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(MSVC)
    add_compile_options(/utf-8)
endif()

add_library(afei-translation MODULE translation.cpp other_text.cpp)
target_link_libraries(afei-translation PRIVATE Qt6::Widgets)
set_target_properties(afei-translation PROPERTIES
    PREFIX ""
    LIBRARY_OUTPUT_DIRECTORY "$<1:${CMAKE_BINARY_DIR}/generic>"
    RUNTIME_OUTPUT_DIRECTORY "$<1:${CMAKE_BINARY_DIR}/generic>"
)

include(CTest)
if(BUILD_TESTING)
    add_executable(afei-translation-smoke test_translation.cpp)
    target_link_libraries(afei-translation-smoke PRIVATE Qt6::Widgets)
    add_dependencies(afei-translation-smoke afei-translation)
    add_test(NAME afei-translation-smoke COMMAND afei-translation-smoke -plugin afei-translation)
    set_tests_properties(afei-translation-smoke PROPERTIES
        ENVIRONMENT "QT_QPA_GENERIC_PLUGINS=;QT_PLUGIN_PATH=${CMAKE_BINARY_DIR};QT_QPA_PLATFORM=offscreen"
    )
endif()
