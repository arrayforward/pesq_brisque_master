# musicq 音质评价系统设计文档

面向**音频直播播放器最终播出音质**的端到端全参考评价系统。本文档面向后续接手的工程师，
算法细节均已对照 `tools/musicq/musicq/` 源码核实。

## 1. 背景与需求

评价对象是自研音频直播播放器在**受限设备**上的最终播出音质。约束条件：

- **设备禁录音**：目标设备禁止麦克风录音权限，也不能 root；可以截屏、可以 adb。
  因此不能从 App 内部 dump PCM（播放器是在售产品形态，不能为测评改代码），
  只能用 scrcpy 的 shell 播放捕获通道（Android 11+）抓 AudioFlinger 混音输出。
- **播放器有网络自适应时间伸缩（TSM）**：为控延迟/抗抖动，播放器用 WSOLA 类算法
  动态微调播放速率，输出相对参考存在**非均匀时间形变**，且形变曲线不可知
  （无伸缩日志、无 API）。全参考评价（ViSQOL/PEAQ）要求逐样本对齐，必须先对齐。
- **为什么不用 PESQ**：P.862 是语音模型（窄带/宽带语音），对音乐内容与 TSM 类
  时间形变都不适用；且对时间对齐极其敏感。ViSQOLAudio 为音乐/通用音频设计，
  内部有一定的时间对齐鲁棒性，更适合本场景。
- **为什么用内嵌导频**：播放器不可改、输出无形变元数据，只有音频本身可用
  → 在专用测试音频里内嵌 chirp 导频标记，从采集信号里把形变曲线测出来再撤销。

## 2. 总体架构

```
┌──────────────────────────────┐
│ Android 设备                  │
│  testplayer.apk（或被测 App） │   播放带导频测试音频
└──────────────▲───────────────┘
               │ adb (播放捕获通道, Android 11+)
               │ 或 蓝牙 A2DP（PC 模拟音响，备选通道）
┌──────────────┴───────────────┐
│ Windows PC                   │
│  scrcpy --no-video --record  │──> cap.mkv ──> ffmpeg 抽取 48k 单声道 cap.wav
│  (内置 tools/scrcpy, ffmpeg) │
│  或 btrecord: A2DP sink +    │──> 回环录制 48k 单声道 cap.wav
│   WASAPI loopback            │
│  或 micrecord: WASAPI 输入    │──> mic/Line-in 录制 48k 单声道 cap.wav
│   （麦克风/Line-in 声学兜底） │
│                              │
│  musicq 引擎 (Python, 冻结为  │   detect: chirp 匹配滤波检测 + leader 聚类
│   engine/musicq-engine.exe)  │   autoscore: 30s 实例切分 + 歌曲识别
│                              │   align: 网格匹配→分段撤销形变→亚采样精对齐
│                              │   score: ViSQOL / PEAQ(可选) / SNR·segSNR·THD+N
│                              │──> report.csv + summary.txt + quality.png
│                              │
│  musicq_tool.exe (Qt6 GUI)   │   三 Tab 外壳，QProcess 调引擎/ scrcpy
└──────────────────────────────┘
```

数据流：`gen`（曲库 → 带导频 test.wav + markers.json）→ 设备播放 →
`capture/extract`（scrcpy → cap.wav）或 `btrecord`（蓝牙 A2DP → cap.wav）
→ `autoscore`（识别+对齐+评分）→ 报告。

### 2.1 采集通道对比

| 通道 | 链路 | 特点 |
|---|---|---|
| scrcpy（默认） | AudioFlinger 混音 → shell 播放捕获 → flac → wav | 数字回采，无编解码损耗；需设备支持 adb 播放捕获（Android 11+） |
| 蓝牙 A2DP（`btrecord`） | 手机蓝牙 → PC A2DP sink（AudioPlaybackConnection）→ 渲染到输出设备 → WASAPI loopback 录制 | 依赖系统/驱动的 A2DP sink 支持（Win10 2004+ 且驱动发布 sink 端点；实测 Intel Wireless Bluetooth@Win11 build 26200 可用，端点动态出现，`open_async` 后必须 `start_async` 才开始监听）；**含 SBC 编解码损耗，与 scrcpy 不是同一链路，分数不可跨通道比**；SBC 带宽 ~15kHz，chirp 频段（10-14k）可能被衰减影响检出率，必要时 `gen --f0/--f1` 降频段 |
| mic/Line-in（`micrecord`） | 手机外放 → PC 麦克风（声学）；或手机耳机口 → PC Line-in（有线直连）→ WASAPI 输入设备录制 | 兜底通道；声学链路对摆放敏感（环境安静、距离角度固定）；注意 PC 端音频增强（降噪/AEC，如 Intel 智音 mic 实测会把外放音乐压至底噪量级）；Line-in 有线质量接近电气直连；分数不可跨通道比。与 btrecord 共用分块写盘（`_record_to_wav`），支持提前停止保留已录部分 |

实测同一播放内容两通道对比（一加 LE2110，夜曲）：scrcpy ViSQOL 4.731 / SNR 56.9dB；
A2DP ViSQOL 4.098 / SNR -13.2dB（SBC 波形重塑使时域 SNR 呈深负值，
chirp 检出数不受影响：两通道均 38/38 峰，识别验证分 1.000 vs 0.38~0.42 均远超 0.3 阈值）。

## 3. chirp 与 leader 设计

### 3.1 常规正文标记

| 参数 | 默认 | 依据 |
|---|---|---|
| 频段 `--f0/--f1` | 10~14 kHz | 高于流行乐主要能量区（掩蔽少），又在 AAC/Opus 常规码率通带（~16kHz）内；mp3 源低通点通常 ≥15kHz |
| 时长 `--dur-ms` | 60 ms | 60ms×4kHz → 匹配滤波处理增益约 24dB，被音乐掩蔽到 -20dB 仍可检出 |
| 电平 `--level` | -24 dBFS 峰值 | 有轻微可闻"滴答"，专用测试音频可接受；更低会被音乐掩蔽漏检 |
| 间隔 `--interval` | 5 s | 漂移跟踪粒度与可闻性的平衡 |
| 首个标记 `--start` | 2 s | 避开片头淡入 |

- 线性 chirp，Tukey 窗（α=0.2）防爆音；`phi=-90` 正弦起始。
- **偶数序号标记上扫、奇数下扫**，检测按方向分开做，网格匹配时奇偶交叉校验，
  把"整数倍间隔平移"的歧义减半（奇数平移直接破坏奇偶一致性）。
- 标记序号在剔除冲突后**重新连续编号并重新分配方向**（保证奇偶性成立）。

### 3.2 leader（周期性 chirp 簇，v3）

| 参数 | 默认 | 说明 |
|---|---|---|
| `--leader-n` | 6 | 簇内 chirp 数 |
| `--leader-gap-ms` | 150 ms | 簇内 chirp 起点间隔（远小于正文 5s → 节奏上可分） |
| `--leader-gap-after` | 0.5 s | 歌头 leader 结束到正文的间隙 |
| `--leader-interval` | 30 s | 正文周期性 leader 间隔（≤0 仅歌头） |

- 歌头一个（t=0，落在静音上）+ 正文每 30s 一个（**叠加**在音乐上，不改变时间轴）。
- 与任一 leader 簇时间过近（<1s）的正文标记让位跳过，避免检测混淆。
- leader 供 autoscore 自动切分：长录音里每次出现标记一次"播放段落起点"，
  30s 粒度天然给出分段报告。

### 3.3 markers.json（v3）

```json
{
  "version": 3,
  "song_id": "<文件名>",
  "sr": 48000, "duration_s": ...,
  "chirp": {f0, f1, dur_ms, level_dbfs, interval_s, start_s},
  "leader": {"start_s": 0, "end_s": 0.81, "n_chirps": 6, "gap_ms": 150,
             "gap_after_s": 0.5, "interval_s": 30,
             "leaders": [{"start_s": 0, "end_s": 0.81}, ...]},
  "markers": [{"index": 0, "time_s": 3.31, "direction": "up"}, ...]
}
```

`gen` 按 version 跳过：旧版（v1/v2）自动重生成。旧 json（无 leader 字段）仍可用于
`align`（leader 剔除靠检测端的簇结构，与 json 无关）。

## 4. 检测算法

### 4.1 匹配滤波检测（chirp.py）

1. 带通到 f0−300 ~ f1+300 Hz（4 阶 butter，filtfilt 零相位）——必须限带，
   否则全带音乐能量把归一化相关的分母顶爆（实测未限带时真峰只剩 0.06）。
2. 归一化互相关（`fftconvolve(x, template[::-1], 'valid')`，除以逐窗能量×模板能量）。
   **静音窗屏蔽**：窗能量低于全局 -60dB 时输出置 0（静音窗数值噪声会被归一化
   放大成假峰，采集开头的静音段实测会骗过阈值）。
3. 阈值 = `max(中位数 + 6·MAD, 0.25)`（实测音乐虚警上限 ~0.2，劣化真峰 ≥0.34）。
   `find_peaks` 最小间距 1.5×chirp 时长（不能用 0.6×标记间隔——会把 150ms
   间隔的 leader 簇压掉，这是实测踩过的坑）。

### 4.2 伸缩导致的系统偏差与精修（align.py）

关键实测发现：**裸模板（未拉伸）匹配有与扫频方向相关的系统偏差**——r=1.03 时
上扫 +7ms / 下扫 -4ms（拉伸 chirp 与模板的互相关峰不在起点）。相邻标记奇偶交替
会使段速率估计直接带上 ~0.2% 误差。同内容互相关有同样偏差（参考窗里的 chirp
也是未拉伸的），换相关对象救不了。

解法是**速率网格 × 位置联合精修**（`_refine_marker`）：以段估计速率为中心做
±0.008（步长 0.004）粗网格 + q(r) 抛物线细定位（直接按 max-q 选网格点会引入
±半格 × 偏差斜率(≈200ms/单位速率) 的选格噪声），每个候选速率用
`resample_poly` 拉伸模板后做局部归一化相关，取最高。chirp-only 受控实验下
精修链误差为 0.000ms；音乐中实测残余 <0.5ms（强高频内容段偶发 ~1ms 抖动，
聚合统计用中位数对此鲁棒）。

## 5. 网格匹配与歌曲识别

### 5.1 锚点投票 + 游走（align.py）

- `_vote_anchors`：**前 30s 窗口**（漂移 <1s，固定偏移投票最干净）与**全曲范围**
  （录音可能只是歌曲中段片段，锚点在前窗外——v3 的 30s 实例大多如此）各投一次，
  合并按 (marker, peak) 去重取高分。
- `_walk_assign`：从锚点向两个方向逐标记推进，用局部速率外推预测下一位
  （`tol = min(0.35×间隔, 0.3+0.05×跨度)`），容忍漏检；隐含速率越界
  （0.85~1.15 外）的峰视为离群剔除。

### 5.2 整数间隔歧义与内容验证

规则 5s 网格 + 漏检造成整数倍间隔的几何歧义：多个平移假设投票同分，
纯几何无法区分。用**内容验证**消歧（`_validate_assignment`）：对匹配范围
首/中/尾的段做撤销形变后的**分块互相关**（0.4s 块逐块 ±15ms 精对齐取平均——
整段单一速率撤销在段内含变速边界时失效，分块是必须的；正确匹配 ~0.6-0.9，
错配 <0.1）。

实测踩过的坑：候选列表被 `max_cand=32` 截断会把正确平移假设挤掉
（真机 30s 中段片段：正确 shift 验证分 1.000，但没进候选）。修复：先 walk
再按假设去重、最多验证 16 个互异假设。这也顺带修了"单候选跳过验证导致
假阳性"的问题（现在唯一候选也会过内容验证）。

### 5.3 歌曲识别

候选只有几首（ref-dir 里的 `*_markers.json`），逐一 `match_grid` 取内容验证分
最高者，低于 0.3 标记 `unknown` 跳过。曲库大时成本 = 候选数 × 实例数 × 几次
分块相关，可接受。

## 6. 对齐（align.py）

1. 检测 + 网格匹配 + 速率网格精修后，每个正文标记得到亚毫秒级采集时刻。
2. 每个标记区间：速率比 `r = Δt_deg/Δt_ref`，sanity clamp 到 [0.9, 1.1]；
   `resample_poly(deg_seg, up, down)`（`up/down = Fraction(1/r).limit_denominator(2000)`）
   把采集段重采样回参考速率，**撤销形变**。
3. 段内精对齐：宽带互相关 ±10ms 整数平移 + 抛物线插值亚采样分数延迟
   （FFT 相移法实现）。
4. 段首尾各去 150ms 保护带（避开 chirp 本体），输出逐段 `ref_NNN.wav`/`deg_NNN.wav`
   对 + `alignment.json`（各段速率比、偏移、漂移曲线数据）。

**"撤销形变 vs 仅平移"的取舍**：段内重采样把速率比归一，PEAQ/ViSQOL 看到的失真
不含时间形变本身；WSOLA 引入的频谱涂抹等真实损伤仍会被扣掉——这正是评价目标。
段内若含速率突变（TSM 跳变落在段中部），单一速率撤销会有残余漂移，该段分数
会被压低——这属于真实 QoE 损伤，不归入对齐误差。

## 7. autoscore 流程

```
cap.wav
  → 全录音 chirp 检测（一次）
  → leader 聚类：相邻峰间隔 ≤1s 成簇，≥4 个 chirp 的簇判定为 leader
  → 相邻 leader 之间为一个实例（≈30s 粒度）
  → 逐实例: 歌曲识别(网格匹配+内容验证) → align → compute_rows
  → loop 判定: 同一 song_id 的本段起始标记序号 < 上一段（歌曲重新开始）→ loop+1
  → write_report: report.csv(逐段) + summary.txt(按歌曲+总体聚合) + quality.png
    + autoscore.json(实例切分/识别明细)
```

- leader 起点精修：簇内每个 chirp 用伸缩匹配模板（速率由簇内首尾间隔估计）精修后
  反推簇首，交替扫频的残余偏差互相抵消，取中位数（实测切点误差 <3ms）。
- 无 leader → 报错退出并提示排查（v3 音源 / 录音时长 > leader 间隔 / chirp 频段
  未被链路滤掉）。
- 实例 <5s 跳过；识别失败标记 unknown 不阻塞其他实例。

## 8. 评分（metrics.py / report.py）

- **ViSQOLAudio（主指标）**：`visqol-python`（PyPI 纯 Python 移植，与 C++ 一致性
  测试中 9/10 完全一致），audio 模式 48kHz，SVR 质量映射。API：
  `VisqolApi(); create(mode="audio"); measure(ref, deg).moslqo`。满分约 4.75
  （identity 实测 4.732）。内部有电平归一化，对采集电平差不敏感。
- **SNR / segSNR / THD+N（纯 numpy）**：计算前先对 deg 做**最小二乘增益校正**
  `g = <ref,deg>/<ref,ref>`，用 `deg/g` 再算误差。起因：真机采集电平跟随设备
  媒体音量，实测比参考低 26.5dB，不校正时 SNR 误报 0.42dB（校正后同段 38~40dB，
  受采集链高频滚降限制）。防御：g 非正/非有限/异常时不校正。
  segSNR 逐帧（20ms/50%重叠）dB 限幅 [-10,35] 取均值；THD+N 对音乐内容只有
  sanity 参考意义（主导谱线谐波拟合）。
- **PEAQ（可选）**：ITU-R BS.1387 有专利约束、无免编译 Windows 方案，不自研不内置；
  通过 subprocess 调外部二进制（约定 `peaq <ref> <deg>`，stdout 解析 ODG），
  缺失自动跳过告警。推荐 WSL 编 GstPEAQ 或 ITU PQevalAudio。
- **聚合**：中位数为主 + P10/P90/均值（边界窗异常分不拖死整体）+ 伸缩统计
  （段数、最大/最小速率比、累计形变量）。

## 9. Qt GUI（tools/musicq_gui）

C++/Qt6 Widgets（不用 QML），VS2022 + CMake 构建，/utf-8，中文 UI。GUI 是纯外壳，
算法全部在 Python 引擎：

- **三 Tab**：音源生成（引擎 `gen`）/ 采集（QProcess 拉起 scrcpy 录制，停止后自动
  引擎 `extract`，静音警告透传）/ 评估（引擎 `autoscore`，解析 autoscore.json +
  report.csv 填实例表，显示 summary.txt 与 quality.png）。
- **引擎双路径解析**（engine.cpp）：优先 `<exe>/engine/musicq-engine.exe`（发布态），
  回退开发态 `tools/musicq/.venv/Scripts/python.exe -m musicq`，都找不到则提示。
- **内置工具检测**（capture_tab.cpp）：启动时检测 `<exe>/tools/scrcpy/scrcpy.exe`
  （含自带 adb.exe）与 `<exe>/tools/ffmpeg/ffmpeg.exe`，存在即填为默认路径；
  子进程环境把这些目录前置 PATH，因此引擎 extract 与 scrcpy 自带的 adb 都能命中。
- ProcRunner：QProcess 封装，UTF-8 行式输出（子进程设 PYTHONIOENCODING=utf-8 +
  引擎入口 `sys.stdout.reconfigure(encoding="utf-8")` 双保险，曾修过 GBK 乱码）。

## 10. 发布与打包

- **PyInstaller 冻结**（one-dir，`engine_main.py` 入口）：
  - `visqol` 的 SVR 模型是数据文件 → `--collect-data visqol`；
  - `soundfile` 自带 libsndfile → `--collect-all soundfile`；
  - **`libsvm-official` 的 `clib.*.pyd` 由 ctypes 动态加载，静态分析发现不了**
    → 必须 `--collect-all libsvm`，否则 ViSQOL 报 "LIBSVM library not found"
    静默退化为跳过（冒烟务必确认 simulate 输出里 `visqol_median` 非 null）；
  - `--exclude-module numba`（冻结 numba/llvmlite 问题多；visqol 有纯 NumPy
    fallback，官方声称结果 bit-exact）。
- **windeployqt**：CMake POST_BUILD 自动拷贝 Qt 运行时。
- **testplayer assets 不入库**：两首 wav 合计 ~107MB，由 `testplayer/copy_assets.bat`
  构建前从生成目录拷贝（默认 `D:\music\musicq_out`），.gitignore 排除。
- 发布目录 `release/`（.gitignore 排除，不入库）：
  `musicq-tool-windows/`（GUI + Qt 运行时 + engine/ + tools/scrcpy + tools/ffmpeg）
  打 zip；`testplayer-debug.apk`；`README.txt` 使用说明。

## 11. 验证结果

### 合成自测（pytest 8/8，无需设备）

| 断言 | 阈值 | 实测 |
|---|---|---|
| 段速率比估计误差（copy/mild/strong 三档） | <0.2% | 最优 4.5e-5，最差 9.6e-4 |
| 固定延迟估计误差（单曲流程，中位数） | <0.5ms | ~0.01ms |
| 原样拷贝 ViSQOL | ≥4.7 | 4.732 |
| ViSQOL 随劣化程度单调 | — | 4.732 > 4.160 > 3.449 > 2.356 |
| autoscore：3 次播放 × 2 周期 leader | 6 实例 | 切分/歌名/循环序号全对 |
| 实例对齐精度 | 速率<0.2%、切点<3ms、标记一致性<1.5ms | 通过 |
| 实例分数 vs 同内容单曲流程参照 | 差 <0.1 | 通过 |

### 真机验证（一加 LE2110，scrcpy 采集）

- 100s 录音：检出 3 个周期 leader（间隔精确 30s），3 段全部识别为夜曲，
  验证分 1.000，ViSQOL 中位 4.731，SNR 中位 56.9dB。
- 10 分钟完整循环录音：海阔天空循环×5 + 夜曲全部正确识别；ViSQOL 中位
  4.665（海阔天空）/ 4.729（夜曲），SNR 中位 49.2 / 56.4dB；testplayer 线性
  播放无形变（速率比恒 1.0，符合预期）。
- 对齐精度（合成形变 ground truth）：速率比误差最优 4.5e-5，标记位置一致性
  亚毫秒（去除参考系常数后）；单曲流程延迟估计误差 ~20µs 量级。
- 顺带发现：该设备采集链 10kHz 以上有滚降（12-14kHz -10.8dB、16-20kHz
  -18.2dB，增益校正后口径），0-10kHz 匹配在 ±0.2dB 内。

## 12. 已知限制

- **chirp 频段风险**：蓝牙 SBC、某些下行音效链路会滤掉 10kHz 以上 → 大量漏检。
  对策：降频段（`--f0/--f1` 到 8-12k 或 6-9k）重新生成；采集前用频谱确认。
- **速率比 clamp [0.9, 1.1]**：超出范围的剧烈伸缩段会被钳制，该段分数偏低且
  摘要里能看到速率贴边；真机 TSM 一般远在此范围内。
- **THD+N 对音乐内容只有 sanity 参考意义**（主导谱线谐波拟合对多音源音乐
  不构成严格 THD+N）；segSNR 有 [-10,35]dB 逐帧限幅，高质量段会顶到 35。
- **段内速率突变**（TSM 跳变落在标记区间中部）会有残余漂移压分——属真实损伤
  而非对齐误差，但排查时要知道这个口径。
- leader 起点精修依赖簇内 chirp 检出数（≥4 成簇）；极端劣化下簇不完整时
  退化为粗检位置（亚秒级），只影响报告时间轴不影响评分。
