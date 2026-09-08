@echo off
rem 构建 testplayer 前执行：从 musicq 生成目录拷贝带 leader 的测试音频到 assets
rem 用法: testplayer\copy_assets.bat [生成目录]   (默认 D:\music\musicq_out)
set SRC=%~1
if "%SRC%"=="" set SRC=D:\music\musicq_out

copy /Y "%SRC%\0911.周杰伦 -夜曲_test.wav" "%~dp0src\main\assets\song1.wav" || goto :fail
copy /Y "%SRC%\Beyond - 海阔天空_test.wav"  "%~dp0src\main\assets\song2.wav" || goto :fail
echo OK: 测试音频已拷贝到 assets
exit /b 0

:fail
echo 失败: 请先生成测试音频 (tools\musicq\mq.bat gen "D:\music" -o "%SRC%")
exit /b 1
