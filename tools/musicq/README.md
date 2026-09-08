# musicq — 音乐直播播放器端到端音质评价管线

评价音频直播播放器的**最终播出音质**。播放器带网络自适应时间伸缩（TSM），
采集信号相对参考存在非均匀时间形变；本工具用内嵌 **chirp 导频标记**的专用测试音频完成对齐，
再用 **ViSQOLAudio**（主）/ PEAQ（可选）/ SNR·segSNR·THD+N（sanity）评分。

## 原理概述

```
音源(mp3) --gen--> 测试wav(内嵌chirp标记) + markers.json
                        |
                 上传到设备，被测播放器播出
                        |
           scrcpy 采集系统播放输出(capture/extract)
                        |
          align: chirp匹配滤波检测 → 网格匹配 → 分段resample撤销形变 → 互相关精对齐
                        |
             score: ViSQOL / PEAQ / SNR → report.csv + summary.txt + quality.png
```

- **chirp 导频**：默认 10–14 kHz、60 ms、峰值 -24 dBFS、每 5 s 一个；
  该频段高于流行乐主要能量区、又在常见编码器通带内，匹配滤波处理增益约 24 dB。
  偶数序号标记上扫、奇数下扫，检测时按奇偶交叉校验，降低网格匹配歧义。
- **leader（chirp 簇）**：**歌头一个 + 正文每隔 30 s 一个**（6 个 chirp、150 ms 等间隔、
  交替扫频），与正文 5 s 间隔的常规标记在节奏上明显区分；与簇过近（<1s）的正文标记
  自动让位跳过。PC 端在长时间录音里按"间距紧密的 chirp 簇"找出所有 leader，
  把录音切成 ~30s 的段，逐段自动识别歌曲并完成对齐打分，全程无需人工切分（见 `autoscore`）。
  参数可调：`--leader-n` / `--leader-gap-ms` / `--leader-gap-after` / `--leader-interval`。
  markers.json v3 起含 `leaders`（全部簇位置）与 `song_id` 字段；旧版 json 仍可用于 `align`。
- **检测是两阶段的**：先带通（f0−300 ~ f1+300 Hz）+ 归一化匹配滤波（MAD 自适应阈值，
  静音窗屏蔽）做粗检；网格匹配后再用**按局部速率拉伸过的模板**做局部相关精修——
  裸模板在时间伸缩下有与扫频方向相关的系统偏差（实测 r=1.03 时上扫 +7ms / 下扫 −4ms），
  伸缩匹配模板可将其消到 0.2ms 以内。
- **对齐用"撤销形变"而非仅平移**：按段估计速率比 `r = Δt_deg/Δt_ref`（clamp 到 [0.9, 1.1]），
  用 `resample_poly`（`Fraction(r).limit_denominator(2000)` 逼近）把采集段重采样回参考速率，
  再做 ±10ms 整数互相关 + 抛物线插值亚采样精对齐。段首尾各去 150 ms 保护带避开 chirp 本体。
  这样 PEAQ/ViSQOL 看到的失真不含时间形变本身，而 WSOLA 引入的频谱涂抹等真实损伤仍会被扣分——
  这正是评价目标。

## 安装

```bash
cd tools/musicq
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
```

- Python ≥ 3.10（开发环境为 3.12）。`visqol-python` 为纯 Python 移植，无编译依赖。
- 采集环节另需 **scrcpy** 与 **ffmpeg**（仅真机采集时需要）：
  `winget install scrcpy`、`winget install ffmpeg`。缺失时 `capture`/`extract` 会打印指引并退出。
- 可选加速：`pip install "visqol-python[accel]"`（numba JIT，约 8× 提速）。

便捷入口：本目录下 `mq.bat`（Windows）等价于 `python -m musicq`，自动使用 `.venv`。

## 完整工作流

```bash
# 1) 批量生成测试音频（输入可为单个 mp3 或目录；默认 D:\music，已生成则跳过）
./mq.bat gen "D:\music" -o "D:\music\musicq_out"
#    产物：<名称>_test.wav（48kHz/16bit/立体声）+ <名称>_markers.json（标记时刻真值）

# 2) 把 *_test.wav 上传到设备，用被测播放器准备播放

# 3) 采集：命令启动后在设备上手动起播，播完按 Ctrl+C
./mq.bat capture -o cap.mkv

# 4) 抽取 48kHz 单声道 wav（自动校验非全静音，防止目标 App 禁止采集白跑）
./mq.bat extract cap.mkv -o cap.wav

# 5) 对齐（chirp 参数默认从 markers.json 读取）
./mq.bat align --ref "D:\music\musicq_out\xxx_test.wav" \
               --markers "D:\music\musicq_out\xxx_markers.json" \
               --deg cap.wav -o aligned_out
#    产物：逐段 ref_NNN.wav / deg_NNN.wav + alignment.json（各段速率比、偏移、漂移曲线）

# 6) 评分与报告
./mq.bat score --dir aligned_out                 # 仅 ViSQOL + SNR 系
./mq.bat score --dir aligned_out --peaq /path/to/peaq_binary   # 追加 PEAQ
#    产物：report.csv（逐段）+ summary.txt（中位数/P10/P90/均值 + 伸缩统计）+ quality.png
```

无设备自检（合成形变全链路，验证对齐精度与分数单调性）：

```bash
./mq.bat simulate --src "D:\music\0927.痴心绝对-李圣杰.mp3" -o simulate_out
.venv/Scripts/python.exe -m pytest tests/ -v
```

## 自动识别 + 一键打分（autoscore，推荐）

测试音频带 leader 后，长时间录音可以全自动处理：

```bash
./mq.bat autoscore cap.wav --ref-dir "D:\music\musicq_out" -o report_out
```

流程：

1. 检测录音中全部 chirp 峰，把"间距紧密的簇"（≤1s 间隔聚类、≥4 个 chirp）识别为 leader
   （v3 测试音频：歌头一个 + 正文每 30s 一个，参数见 `--leader-interval`），
   相邻 leader 之间为一段（约 30s，粒度更细）；
2. 逐段做**歌曲识别**：用 `--ref-dir` 里的全部候选歌曲逐一做网格匹配 + 内容验证，
   取验证分最高者；低于阈值（0.3）标记 `unknown` 并跳过；
   循环序号按"同一歌曲本段起始标记序号比上一段小（歌曲重新开始）"递增；
3. 识别成功后走与 `align`/`score` 完全相同的对齐评分流程（同一套 `align()`/`compute_rows()`）；
4. 报告：`report.csv` 逐段（含 instance/song_id/loop/录音起止时间列），
   `summary.txt` 按歌曲聚合 + 总体聚合（中位数/P10/P90/均值），`quality.png` 质量曲线，
   `autoscore.json` 记录实例切分与识别明细；每段的对齐中间产物在 `inst_XX_<歌名>/aligned/`。

无 leader 时报错退出并提示排查（确认 v3 音源、录音时长大于 leader 间隔、chirp 频段未被滤掉）。

## testplayer 真机工作流

`testplayer/` 是极简测试播放器（assets 内置带 leader 的 song1/song2.wav，开屏自动播放、双曲循环）：

```bash
# 构建并安装（仓库根目录）。assets 的测试音频不入库（>100MB），先拷贝：
testplayer/copy_assets.bat           # 从 D:\music\musicq_out 拷贝 song1/song2.wav
./gradlew :testplayer:assembleDebug
adb install -r testplayer/build/outputs/apk/debug/testplayer-debug.apk

# 设备上打开 testplayer（自动开始双曲循环播放），PC 端采集
cd tools/musicq
./mq.bat capture -o cap.mkv          # 采集一段时间后 Ctrl+C
./mq.bat extract cap.mkv -o cap.wav

# 一键自动识别 + 打分（ref-dir 用生成 testplayer 音频的那个输出目录）
./mq.bat autoscore cap.wav --ref-dir "D:\music\musicq_out" -o report_out
```

每次循环都会在录音里留下一个 leader，autoscore 据此区分"哪首歌、第几遍"，无需人工切分。

## chirp 参数调优

`gen` 子命令支持（`align` 默认从 markers.json 读取，无需重复指定）：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--f0` / `--f1` | 10000 / 14000 Hz | 频段。源低通点低（如低码率 mp3 < 10kHz 能量弱）时可降到 6–9kHz，但要避开音乐主能量区 |
| `--dur-ms` | 60 | 时长。加长 → 处理增益高、更抗掩蔽；缩短 → 时间定位更准 |
| `--level` | -24 dBFS | 电平。太低会被音乐掩蔽漏检，太高可闻"滴答"更明显 |
| `--interval` | 5 s | 标记间隔。密 → 跟踪漂移更细、段更短；疏 → 对可闻性影响小 |
| `--start` | 2 s | 首个标记时刻，避开片头淡入 |

检测端阈值是 MAD 自适应的，一般无需调；若漏检多，先确认采集链没有把 10kHz 以上滤掉
（蓝牙 SBC、某些下行特效会），再考虑把 `--f0/--f1` 降到 8–12kHz 重新生成。

## PEAQ 二进制获取

PEAQ（ITU-R BS.1387）无可直接 pip 的实现，本工具通过 subprocess 调外部二进制
（约定接口：`peaq <ref.wav> <deg.wav>`，stdout 中含 `ODG = x.xxx`），缺失时自动跳过并告警：

1. **GstPEAQ（WSL，推荐）**：`wsl` 内 `sudo apt install gstreamer1.0-tools` 后编译
   [GstPEAQ](https://github.com/HSU-ANT/peaq)；或
2. **ITU PQevalAudio**：ITU-T 官方参考实现（需申请/购买许可），Windows 可编译出 exe。

## 目录结构

```
tools/musicq/
├── README.md / requirements.txt / mq.bat
├── musicq/
│   ├── cli.py        # argparse 入口: gen / capture / extract / align / score / simulate
│   ├── chirp.py      # chirp 生成与匹配滤波检测（MAD 自适应阈值）
│   ├── generate.py   # 批量加工测试音频
│   ├── capture.py    # scrcpy 采集 + ffmpeg 抽取
│   ├── align.py      # 网格匹配 + 分段消形变 + 精对齐
│   ├── metrics.py    # visqol / peaq(外部二进制) / snr / segsnr / thd+n
│   ├── report.py     # 分段分 + 聚合 + 质量曲线
│   └── simulate.py   # 合成劣化与全链路自测
└── tests/test_pipeline.py
```
