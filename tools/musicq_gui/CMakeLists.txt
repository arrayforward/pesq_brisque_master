cmake_minimum_required(VERSION 3.20)
project(musicq_tool LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(MSVC)
    # 源码含中文 UI/注释 (UTF-8), 避免按系统代码页解析出错
    add_compile_options(/utf-8)
endif()

set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTOUIC ON)
set(CMAKE_AUTORCC ON)

find_package(Qt6 REQUIRED COMPONENTS Widgets)

set(GUI_SOURCES
    gui/main.cpp
    gui/main_window.h gui/main_window.cpp
    gui/engine.h gui/engine.cpp
    gui/proc_runner.h gui/proc_runner.cpp
    gui/gen_tab.h gui/gen_tab.cpp
    gui/capture_tab.h gui/capture_tab.cpp
    gui/eval_tab.h gui/eval_tab.cpp
    gui/chart_widget.h gui/chart_widget.cpp
)

add_executable(musicq_tool WIN32 ${GUI_SOURCES})
target_link_libraries(musicq_tool PRIVATE Qt6::Widgets)

# windeployqt (自动拷贝 Qt 运行时 DLL)
find_program(WINDEPLOYQT windeployqt HINTS "${Qt6_DIR}/../../../bin")
if(WINDEPLOYQT)
    add_custom_command(TARGET musicq_tool POST_BUILD
        COMMAND ${WINDEPLOYQT} --no-translations $<TARGET_FILE:musicq_tool>)
endif()
