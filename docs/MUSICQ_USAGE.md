# 音乐播放音质评价系统 — 使用说明

面向**音频直播播放器最终播出音质**的端到端评价。播放器带网络自适应时间伸缩（TSM），
每次播放的时间轴都不一样，无法直接和原始音频对比；本系统通过预埋 **chirp 导频标记**的
专用测试音频解决对齐问题，再用 **ViSQOLAudio**（全参考感知评价，音乐适用）打分。

## 系统组成

```
┌─────────────────────────┐        ┌──────────────────────────────┐
│  Android 设备            │        │  Windows PC                   │
│  testplayer.apk          │  adb   │  musicq_tool.exe (Qt6 GUI)    │
│  内置chirp测试音频        │ ─────> │  ├─ 音源生成 (gen)            │
│  开屏自动播放、双曲循环    │ scrcpy │  ├─ 采集 (scrcpy→wav)         │
│  (或被测直播App播放)      │ 音频   │  └─ 评估 (autoscore一键打分)   │
└─────────────────────────┘        └──────────────────────────────┘
```

| 组件 | 位置 | 说明 |
|---|---|---|
| testplayer.apk | `release/testplayer-debug.apk` | 极简测试播放器，内置夜曲/海阔天空两首带标记测试音频 |
| Windows 程序 | `release/musicq-tool-windows.zip` | 解压即用，含 GUI + 冻结算法引擎，不依赖本机 Python |
| CLI 引擎（开发态） | `tools/musicq/` | Python 管线，功能与 GUI 一致，可脚本化 |
| GUI 源码 | `tools/musicq_gui/` | C++/Qt6，需 VS2022 + Qt 6.8.3 构建 |

评价链路口径：**设备播放输出（AudioFlinger 混音后、进 HAL 前）**，经 scrcpy 数字回采，
不含 DAC/扬声器/环境噪声；时间伸缩被对齐算法撤销，WSOLA 引入的频谱涂抹等真实损伤保留扣分。

## 快速开始（发布包，三步）

### 0. 前置条件

scrcpy（含 adb）与 ffmpeg 均已内置于发布包（`tools/scrcpy/`、`tools/ffmpeg/`），
GUI 默认使用，**开箱即用，PC 端无需安装任何工具**。
只需 Android 设备开启 **USB 调试**（Android 11 及以上）并连接成功
（「采集」Tab 点「刷新设备」可见即可，adb 用的就是内置 scrcpy 自带的）。
禁录音的受限设备不影响本流程（走的是 shell 播放捕获通道，不用麦克风权限）。

### 1. 安装并启动测试播放器

```bat
adb install -r testplayer-debug.apk
```

设备上打开 **TestPlayer**，自动开始双曲循环播放（界面显示当前曲目和进度）。
播放期间保持 App 前台、屏幕常亮。

> 如果是评价真实直播 App：跳过本步，把第 2 步生成的 `*_test.wav` 上传到直播链路，
> 在被测 App 里播放即可。

### 2. 采集

三种采集通道（GUI「采集」Tab 顶部下拉选择）：

| 通道 | 链路 | 适用场景 | 注意 |
|---|---|---|---|
| **scrcpy（默认）** | AudioFlinger 混音 → shell 播放捕获 → flac → wav | 首选；数字回采无编解码损耗 | 需 adb + Android 11+ |
| **蓝牙 A2DP** | 手机蓝牙 → PC A2DP sink → 回环录制 | scrcpy 不可用的设备 | 含 SBC 编解码损耗；分数不可跨通道比；依赖系统/驱动的 sink 支持 |
| **麦克风 / Line-in** | 手机外放 → PC mic（声学）；或手机耳机口 → PC Line-in（有线） | 前两通道都不可用时的兜底 | 声学：环境安静、mic 距离角度固定保证可重复性；有线 Line-in 质量接近电气直连；分数不可跨通道比 |

**方式 A：scrcpy（默认，USB 数字回采）**

打开 `musicq_tool.exe` →「采集」Tab，采集方式选「scrcpy（USB 数字回采）」：

1. 点「刷新设备」确认设备在线；
2. 点「开始采集」，确认设备已在播放测试音频；
3. 采集足够时长（**≥35s**，保证至少含一个周期性 leader——v3 测试音频每 30s 一个；
   建议覆盖 **2 遍以上完整循环**约 15~20 分钟以评估稳定性），点「停止并抽取」；
4. 自动抽出 48kHz 单声道 wav 并跳转到评估 Tab。日志若提示"全静音"，
   说明播放链路没被采到（见常见问题）。

**方式 B：蓝牙 A2DP（scrcpy 不可用的设备）**

PC 模拟成蓝牙音响（A2DP sink），手机蓝牙把声音推给 PC，PC 回环录制。**两步操作**：

1. 「采集」Tab 采集方式选「蓝牙 A2DP（PC 模拟音响）」，设置录制时长，
   点「**打开蓝牙 Sink**」——PC 对外呈现为蓝牙音响，状态栏显示"等待手机连接…"；
2. 手机「设置→蓝牙」连接本电脑（首次需在列表里点一次配对/连接，已配对过的直接点条目），
   状态变为"手机已连接"后点「**开始录制**」；录制期间 sink 保持，结束后自动出 wav 并跳转评估 Tab。
   录制为**分块实时写盘**：中途崩溃/断电/提前停止，已录部分都保留在盘上——
   录制中点「停止并抽取」= 正常结束本次录制，对已录部分做静音校验后照常送评估
   （需 ≥35s 保证含周期性 leader，太短会在评估阶段提示无 leader）。

> 依赖系统/驱动的 A2DP sink 支持（Windows 10 2004+ 且蓝牙驱动发布 sink 端点）。
> 实测 Intel Wireless Bluetooth（Win11 build 26200）可用；
> 若提示"未检测到 A2DP sink 端点"则本机驱动不支持，请用 scrcpy 方式。
> CLI 等价流程：`mq btsink`（保持运行，JSON 行状态输出）+ `mq btrecord --no-sink`。

**A2DP 通道的两个固有注意点：**

- A2DP 默认走 SBC 编码，采集信号**包含蓝牙编解码损耗**，与 scrcpy 数字回采
  不是同一条链路，**分数不能跨通道直接比**（实测同一播放：scrcpy ViSQOL 4.73
  vs A2DP 4.10；SNR 系指标因编解码波形重塑会呈深负值，属该链路固有特性）；
- SBC 高频带宽有限（~15kHz），chirp 的 10-14kHz 频段可能被衰减影响检出率；
  漏检多时先用频谱确认，必要时用 `gen --f0/--f1` 降频段（如 8-12kHz）重新生成测试音频。

**方式 C：麦克风 / Line-in（兜底，声学/有线）**

前两个通道都不可用时：手机扬声器外放 → PC 麦克风（声学），或手机耳机口 → PC Line-in（有线直连）。

1. 「采集」Tab 采集方式选「麦克风 / Line-in（声学采集）」，输入设备下拉框自动枚举
   （选实际使用的 mic/Line-in；「刷新」重新枚举）；
2. 设置录制时长（0=不限时长，随时「停止并抽取」结束）；
3. 点「开始采集」，手机开始外放播放（或接好线播放）；
4. 与 A2DP 同样支持提前停止保留已录部分（分块实时写盘），结束自动校验送评估。

注意：声学通道对摆放敏感——**环境安静、手机与麦克风距离/角度固定**以保证多次
测量可重复；PC 端若开着"降噪/回声消除"类音频增强会压制音乐信号（本机 Intel 智音
mic 实测会把外放音乐压到接近底噪，导致 leader 无法检出——真机声学验证请确认
输入设备行为）。有线 Line-in 无此问题，质量接近电气直连。**声学/有线链路与数字
通道口径不同，分数不可跨通道比。**

### 3. 评估

「评估」Tab：确认采集 wav 和参考目录（含 `*_test.wav` + `*_markers.json`，
testplayer 两首歌的参考文件在生成输出目录，如 `D:\music\musicq_out`）→「开始评估」。

引擎自动完成：leader 检测 → 循环实例切分 → 歌曲识别 → 逐段对齐 → 打分。
结果显示每次循环一行（歌名/起止时间/ViSQOL 中位数/SNR）+ 聚合统计 + 质量曲线。

## 报告解读

输出目录下的完整报告：

| 文件 | 内容 |
|---|---|
| `report.csv` | 逐段明细：实例号、歌名、循环次数、段号、ViSQOL/SNR/segSNR/THD+N |
| `summary.txt` | 按歌曲聚合 + 总体聚合：**中位数 / P10 / P90 / 均值** + 伸缩统计（调速次数、最大速率比、累计形变量） |
| `quality.png` | 质量随时间的曲线，可直观看到"网络抖动后质量掉一截又恢复" |
| `autoscore.json` | 实例切分与歌曲识别明细（排查用） |
| `inst_XX_<歌名>/aligned/` | 每实例的对齐中间产物（逐段 ref/deg wav、漂移曲线） |

**分数怎么看：**

- ViSQOL 满分约 4.75；≥4.5 接近透明，4.0~4.5 良好，3.0~4.0 有明显可闻损伤，<3.0 较差；
- **聚合看中位数**，P10 反映最差时刻（通常对应网络抖动/伸缩频繁段）；
- 分数用于**同一内容、不同版本/参数播放器的横向对比**，不要跨曲目比绝对值；
- 伸缩统计是独立 QoE 维度：调速越频繁、幅度越大，听感越差，即使对齐后分数尚可。

## 常见问题

| 现象 | 原因与处理 |
|---|---|
| 采集结果为全静音 | 目标 App 设了 `allowAudioPlaybackCapture=false`，或设备策略连 shell 播放捕获也禁了。换 testplayer 验证链路；确认 scrcpy ≥ 2.0、Android ≥ 11 |
| 采集电平远低于参考 | 采集电平跟随设备**媒体音量**，建议采集前把媒体音量拉满。电平差异不影响 ViSQOL（内部有电平归一化）；SNR/segSNR/THD+N 已做最小二乘增益校正（`g=<ref,deg>/<ref,ref>`），电平差不会误伤指标 |
| autoscore 识别不出歌曲（全 unknown） | 采集电平过低、chirp 频段被链路滤掉（蓝牙 SBC、下行音效）。检查采集 wav 频谱 10~14kHz 是否有能量；必要时降 chirp 频段重新生成（见参数调优） |
| 提示"未检测到任何 leader" | 录音短于 leader 间隔（v3 测试音频每 30s 一个）。保证录音 ≥35s；旧版（v2，仅歌头有 leader）测试音频需重新用 v3 生成 |
| 报告里 `peaq_odg: 不可用` | 正常。PEAQ 是专利受限技术，无自由分发的实现，发布包未内置；主指标 ViSQOL 不受影响。需要 PEAQ 见下文"进阶：接入 PEAQ" |
| 识别对但大量漏段 | 伸缩过于剧烈（速率比超出 [0.9, 1.1] clamp）或采集中断。看 autoscore.json 明细 |
| 分数异常低但听着不差 | 先用对齐产物自查：听 `inst_XX/aligned/deg_NNN.wav` 是否真有失真；确认参考用的是**同一份带标记的 test.wav** |
| GUI 提示找不到引擎 | 发布目录结构被破坏，`engine/` 必须与 `musicq_tool.exe` 同级 |
| 与开发态 CLI 分数有 ~0.15 差异 | 发布引擎走纯 NumPy 路径（无 numba），浮点细节差异。**对比实验固定用同一引擎** |
| 评估很慢 | ViSQOL 逐段计算约 0.5×实时，属正常；开发态可装 `visqol-python[accel]` 加速约 8× |

## 进阶：chirp 参数调优

测试音频生成参数（GUI「音源生成」Tab / CLI `gen`）：

| 参数 | 默认 | 什么时候调 |
|---|---|---|
| 频段 `--f0/--f1` | 10~14 kHz | 链路低通点低（低码率编码、蓝牙）时降到 8~12k 或 6~9k，避开音乐主能量区 |
| 时长 `--dur-ms` | 60 ms | 加长更抗音乐掩蔽；缩短时间定位更准 |
| 电平 `--level` | -24 dBFS | 漏检多就提高（更明显的"滴答"声），反之降低 |
| 标记间隔 `--interval` | 5 s | 调密可更细地跟踪漂移；调疏减少对听感的影响 |

leader（启动 chirp 簇）参数 `--leader-n/--leader-gap-ms/--leader-gap-after` 一般不用动。

## 进阶：CLI 用法（脚本化/批处理）

```bat
cd tools\musicq

:: 生成测试音频（输入可为单曲或目录）
mq.bat gen "D:\music" -o "D:\music\musicq_out"

:: 采集（设备上手动起播，Ctrl+C 停止）→ 抽取 wav
mq.bat capture -o cap.mkv
mq.bat extract cap.mkv -o cap.wav

:: 一键自动识别 + 打分（推荐）
mq.bat autoscore cap.wav --ref-dir "D:\music\musicq_out" -o report_out

:: 无设备自检（合成形变全链路验证）
mq.bat simulate --src "D:\music\0927.痴心绝对-李圣杰.mp3" -o simulate_out
```

单曲手动对齐打分（不依赖 leader，用于排查）：`align` + `score`，详见 `tools/musicq/README.md`。

## 进阶：接入 PEAQ

PEAQ（ITU-R BS.1387）显示"不可用"是预期行为：它是**专利受限技术**，ITU 官方参考实现
（PQevalAudio）需申请许可，早年开源实现 EAQUAL 因专利投诉下架，PyPI 上也没有可用包，
因此发布包不内置。本工具通过外部二进制接入（`--peaq /path/to/binary`，
约定接口 `peaq <ref.wav> <deg.wav>`，stdout 含 `ODG = x.xxx`）：

1. **WSL 编译 GstPEAQ**（免费，内部测试用途）：WSL 装 GStreamer 开发包后编译
   [GstPEAQ](https://github.com/HSU-ANT/peaq)，再用脚本包装 `wsl.exe gstpeaq ...` 供 `--peaq` 调用；
2. **ITU 官方许可**：商用场景购买 PQevalAudio 授权，编译 Windows exe。

缺省时自动跳过，不影响 ViSQOL 主指标（ViSQOLAudio 本身即面向音乐感知评价，
与 PEAQ 作用相当，无专利问题）。

## 相关文档

- `tools/musicq/README.md` — 管线原理（chirp 设计、对齐算法）与完整 CLI 参考
- `tools/musicq_gui/README.md` — GUI 构建与引擎冻结方法
- `README.md` 第八节 — 项目总览中的本工具定位
