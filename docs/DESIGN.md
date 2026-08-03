# 设计文档

## 1. 总体架构

```
┌────────────────────────────────────────────────────┐
│ MainActivity (4 Tab: 画面+音频/音频/日志/配置)      │
│ FrameViewActivity (单帧查看/另存)                   │
└───────────────▲────────────────────────────────────┘
                │ 广播事件 (brisque_stats/frame/mos/audio/log)
┌───────────────┴────────────────────────────────────┐
│ CaptureService (前台服务, 类型 mediaProjection)      │
│  ├─ 倒计时调度 (可配置 0~30s)                       │
│  ├─ 截屏: MediaProjection→VirtualDisplay→ImageReader│
│  ├─ 录音: AudioPlaybackCapture→AudioRecord 16kHz    │
│  └─ 打分调度: 采集结束后逐帧/逐段计算                │
└───────────────▲────────────────────────────────────┘
                │
┌───────────────┴────────────────────────────────────┐
│ 评分引擎                                            │
│  ├─ Brisque.java      (纯 Java, MSCN+AGGD+libsvm)   │
│  ├─ MosNet.java→libpesqjni(mosnet.cpp) (C++ 推理)   │
│  ├─ P563.java→libp563 (ITU-T P.563 参考 C 代码)     │
│  ├─ AudioQuality.java (频谱指标: 带宽/底噪/动态/削波)│
│  └─ Pesq.java→libpesqjni (P.862 全参考, 备用未启用) │
└────────────────────────────────────────────────────┘
```

## 2. 采集设计

### 截屏
- 用户授权 `createScreenCaptureIntent` → `getMediaProjection`（在 `startForeground` 之后调用，Android 10 的时序要求）
- `VirtualDisplay`(AUTO_MIRROR) 输出到 `ImageReader`(RGBA_8888, 3 缓冲)
- 每 100ms `acquireLatestImage` 取最新帧，双线性降采样到 512 宽
- 每帧同时保存：灰度字节数组（内存，供打分）+ JPEG 文件（`files/frames/`，供查看/另存）

### 录音（三模式 + 自动降级）
- **系统回放采集**：`AudioPlaybackCaptureConfiguration` 匹配 USAGE_MEDIA/GAME/UNKNOWN，绑定同一 MediaProjection 令牌，数字链路保真最高
- **麦克风**：`AudioRecord(MIC)` 录外放链路，任何设备可用
- **自动模式（默认）**：优先回放采集；以下任一情况自动降级为麦克风并继续：
  1. `AudioRecord` 构造/初始化失败（设备不支持回放采集）
  2. 录制 2 秒后全程最大振幅为 0（目标 App 禁止被采集，录到静音）
- 录音线程全部代码包在 try/catch 内：无 RECORD_AUDIO 权限、系统拒绝 audio policy 注册等场景只上报错误，不崩溃
- 结果卡片与日志标注实际采集模式，避免两种链路的分数被直接比较

### 时序
```
点击开始 → 权限检查 → 投屏授权 → startForeground → getMediaProjection
→ 倒计时 N 秒(通知栏同步) → 采集 M 秒(截屏+录音并行, 每秒 tick 上报)
→ 停止采集 → 逐帧 BRISQUE 打分(实时上报每帧分数) → 统计(去极值)
→ 音频 MOSNet/P.563 推理(长录音按 10s 分段) → done
```

## 3. 评分算法

### BRISQUE（画面，纯 Java）
1. 灰度化：`0.2125R+0.7154G+0.0721B`，归一化到 [0,1]
2. MSCN：7×7 高斯核（σ=7/6）分离卷积求局部均值/方差，`MSCN=(I-μ)/(σ+1/255)`
3. AGGD 拟合：MSCN 本体（α, 方差）+ 4 方向相邻乘积（α, mean, σl², σr²）= 18 维/尺度
4. 双三次降采样 1/2 后再取 18 维，共 36 维
5. [-1,1] 归一化（LIVE min/max）→ libsvm RBF（γ=0.05，770 SV）回归，输出 0~100（越低越好）

验证：官方样例图（4096×2160 自然照片）得分 34.1，与参考实现一致。

### MOSNet（音频，C++ NDK）
- 预处理：16kHz PCM → hamming 窗 STFT（512 点，hop 256，reflect 填充）→ 幅度谱 (T,257)
- 网络：Reshape → 4×[3×Conv2D(3×3,same,relu)，每组第三层 stride(1,3)] → (T,4,128) → (T,512) → BiLSTM(128, Keras 权重布局 i/f/c/o，双 bias 相加，反向时间轴翻转) → Dense(128,relu) → Dense(1) → 帧平均
- 权重：Keras `cnn_blstm.h5` 经离线转换工具（Java + jhdf）导出为自定义二进制（magic "MOSW" + 张量表 + float32），assets 内置 4.7MB

验证：干净 16kHz 语音 MOS=3.48。

### P.563（音频，ITU 官方 C 参考实现）
- 16kHz 录音 → 31 阶 FIR 低通（3.4kHz 截止）→ 2 倍抽取到 8kHz
- module1/2/3（发声特征、噪声/中断检测、感知映射）→ PostProcessMovs → MOS-LQO(1~4.5)
- JNI 封装 `#include "p563.c"`（`#define main` 重命名避免冲突）直接复用官方入口

验证：干净语音 MOS-LQO=4.10。

### 频谱指标（AudioQuality）
2048 点 FFT（hop 1024）：95%/99% 频谱滚降（带宽）、最安静 10% 帧估算底噪 SNR、RMS 十分位动态范围、削波率、有效发声占比、平均电平。只出数值，不合成总分。

### PESQ（P.862，备用）
ITU 官方参考代码已编译进 `libpesqjni`，`Pesq.measure(sampleRate, ref, deg, wideband)` 可用；因需参考音频未接入界面。

## 4. 关键问题与解决

| 问题 | 解决 |
|---|---|
| `ForegroundServiceDidNotStartInTimeException` | 进入服务立即 startForeground，重活（模型加载）全部放后台线程 |
| `SecurityException: Media projections require...`(Android 10) | startForeground(带类型) 必须先于 getMediaProjection |
| 某些机型倒计时结束"跳后台" | 根因：`AudioRecord` 构造在 try 外，回放采集不支持时 FATAL 崩溃；已修复为全流程捕获 |
| 切走后进度"停止" | 接收器在 onCreate 注册、onDestroy 注销，不再跟随 onPause |
| 帧内存占用 | 降采样到 512 宽，灰度存 byte[]，JPEG 落盘 |
| 麦克风模式合规 | 前台服务类型声明 `mediaProjection\|microphone` + `FOREGROUND_SERVICE_MICROPHONE` 权限 |
| EMUI 安装确认框 | adb 安装需在手机上点"继续安装" |

## 5. 域适配说明（为何分数"不合理"）

- BRISQUE 训练域为自然照片失真（LIVE 库），UI 截图/文字/纯色 → 统计特性偏离 → 分数系统性偏高（80~110）。适用于同内容横向对比
- MOSNet/P.563 为语音模型，音乐输入 → 分数系统性偏低（音乐 MOS 约 1.3~2.3）。音频评估建议使用语音内容
