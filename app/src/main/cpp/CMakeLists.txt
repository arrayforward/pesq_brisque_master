cmake_minimum_required(VERSION 3.22.1)
project(pesqjni C CXX)

add_library(pesqjni SHARED
    pesq_jni.c
    mosnet.cpp
    dsp.c
    pesqdsp.c
    pesqmod.c
)

find_library(log-lib log)

set_target_properties(pesqjni PROPERTIES CXX_STANDARD 17)

target_link_libraries(pesqjni m ${log-lib})

file(GLOB P563_SOURCES p563/*.c)
list(REMOVE_ITEM P563_SOURCES ${CMAKE_CURRENT_SOURCE_DIR}/p563/p563.c)

add_library(p563 SHARED
    p563_jni.c
    ${P563_SOURCES}
)

target_include_directories(p563 PRIVATE p563)

target_compile_options(p563 PRIVATE -Wno-error=format-security -Wno-format-security)

target_link_libraries(p563 m ${log-lib})
