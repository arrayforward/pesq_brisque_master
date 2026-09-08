# musicq_gui — musicq 音质评估工具（C++/Qt6 Widgets 外壳）

算法引擎是 `tools/musicq`（Python）；本 GUI 通过 **QProcess** 调用
PyInstaller 冻结的 `engine/musicq-engine.exe`（或开发态回退 `.venv` 的 `python -m musicq`）。

## 构建

```bash
cd tools/musicq_gui
cmake -B build -G "Visual Studio 17 2022" -A x64 \
      -DCMAKE_PREFIX_PATH=D:/tools/Qt/6.8.3/msvc2022_64
cmake --build build --config Release
```

产物 `build/Release/musicq_tool.exe`（POST_BUILD 已自动跑 windeployqt）。

## 引擎冻结（发布前）

```bash
cd tools/musicq
.venv/Scripts/python.exe -m PyInstaller --noconfirm --clean --onedir \
    --name musicq-engine --paths . \
    --collect-data visqol --collect-all soundfile --collect-all libsvm \
    --exclude-module numba --exclude-module tkinter \
    --distpath dist_engine --workpath build_engine engine_main.py
```

注意点：`libsvm-official` 的原生 `clib.*.pyd` 由 ctypes 动态加载，
PyInstaller 静态分析发现不了，必须 `--collect-all libsvm`，否则 ViSQOL 报
"LIBSVM library not found" 静默退化为跳过该指标。
冒烟验证：`dist_engine/musicq-engine/musicq-engine.exe simulate -o <tmp>`，
确认输出 JSON 里 `visqol_median` 非 null。

## 运行逻辑

- 引擎解析顺序：`<exe>/engine/musicq-engine.exe` → 开发态 `tools/musicq/.venv`。
  都找不到则在状态栏/弹窗提示。
- 音源生成 Tab：调 `gen`（输入曲库目录、输出目录）。
- 采集 Tab：检查/配置 scrcpy、ffmpeg、adb 路径 → `scrcpy --no-video --no-control
  --record=cap.mkv --audio-codec=flac` 录制（用户在设备上手动起播）→ 停止后自动
  调引擎 `extract` 出 48kHz 单声道 wav（日志含静音校验警告）→ 自动填入评估 Tab。
- 评估 Tab：调 `autoscore`（自动识别歌曲/循环）→ 实例表（歌名/循环/起止/
  ViSQOL 中位/SNR 中位）+ summary.txt 聚合文本 + quality.png 质量曲线。
