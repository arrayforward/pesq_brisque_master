"""chirp 导频标记的生成与匹配滤波检测。"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
from scipy import signal


@dataclass
class ChirpConfig:
    """chirp 导频参数（均可通过 CLI 覆盖）。"""
    f0: float = 10000.0        # 起始频率 Hz（上扫）
    f1: float = 14000.0        # 终止频率 Hz（上扫）
    dur_ms: float = 60.0       # 单个 chirp 时长
    level_dbfs: float = -24.0  # 峰值电平
    interval_s: float = 5.0    # 标记间隔
    start_s: float = 2.0       # 首个标记时刻

    def to_dict(self):
        return asdict(self)


@dataclass
class LeaderConfig:
    """leader（chirp 簇）参数：歌头一个 + 正文每隔 interval_s 一个。

    节奏密集（150ms 间隔），与正文 5s 间隔的常规标记明显区分。
    """
    n_chirps: int = 6          # 簇内 chirp 数
    gap_ms: float = 150.0      # 簇内 chirp 起点间隔（远小于正文 5s）
    gap_after_s: float = 0.5   # 歌头 leader 结束到正文的间隙
    interval_s: float = 30.0   # 正文周期性 leader 间隔（<=0 关闭，仅歌头）

    def to_dict(self):
        return asdict(self)


def make_chirp(sr: int, cfg: ChirpConfig, up: bool = True) -> np.ndarray:
    """生成一个线性 chirp，峰值归一到 level_dbfs，两端加 Tukey 窗防爆音。"""
    dur_s = cfg.dur_ms / 1000.0
    n = int(round(sr * dur_s))
    t = np.arange(n) / sr
    fa, fb = (cfg.f0, cfg.f1) if up else (cfg.f1, cfg.f0)
    # phi=-90 → 正弦起始，从 0 开始
    x = signal.chirp(t, fa, dur_s, fb, method="linear", phi=-90)
    x = x * signal.windows.tukey(n, alpha=0.2)
    peak = np.max(np.abs(x)) or 1.0
    amp = 10.0 ** (cfg.level_dbfs / 20.0)
    return (x / peak * amp).astype(np.float32)


def marker_times(total_s: float, cfg: ChirpConfig) -> list[dict]:
    """生成标记时刻表：偶数序号上扫、奇数序号下扫。"""
    out = []
    i = 0
    t = cfg.start_s
    margin = cfg.dur_ms / 1000.0 + 0.5
    while t + margin < total_s:
        out.append({"index": i, "time_s": round(t, 6),
                    "direction": "up" if i % 2 == 0 else "down"})
        i += 1
        t += cfg.interval_s
    return out


def _matched_filter_norm(x: np.ndarray, template: np.ndarray) -> np.ndarray:
    """归一化互相关（匹配滤波），输出下标 i 对应 chirp 起点 i。"""
    n = len(template)
    corr = signal.fftconvolve(x, template[::-1], mode="valid")
    energy = signal.fftconvolve(x * x, np.ones(n), mode="valid")
    denom = np.sqrt(np.maximum(energy, 0.0) * float(np.sum(template ** 2)))
    out = corr / (denom + 1e-12)
    # 静音窗能量趋零，归一化会把数值噪声放大成假峰，直接置 0
    out[energy < 1e-6 * np.max(energy)] = 0.0
    return out


def _bandpass(x: np.ndarray, sr: int, cfg: ChirpConfig) -> np.ndarray:
    """带通到 chirp 频段附近（匹配滤波前必须限带，否则全带音乐能量会淹没归一化相关）。"""
    lo = max(cfg.f0 - 300.0, 100.0)
    hi = min(cfg.f1 + 300.0, sr / 2 - 100.0)
    sos = signal.butter(4, [lo, hi], btype="band", fs=sr, output="sos")
    return signal.sosfiltfilt(sos, x)


def detect_chirps(x: np.ndarray, sr: int, cfg: ChirpConfig, up: bool,
                  mad_k: float = 6.0, min_height: float = 0.25,
                  _bandpassed: bool = False) -> np.ndarray:
    """在单声道信号 x 中检测指定方向的 chirp，返回起点时刻数组（秒）。

    阈值 = max(中位数 + mad_k·MAD, min_height)：MAD 自适应跟踪音乐背景，
    min_height 兜底（实测音乐虚警上限 ~0.2，劣化 chirp 真峰 ≥0.34）。
    """
    if not _bandpassed:
        x = _bandpass(x.astype(np.float64), sr, cfg)
    template = make_chirp(sr, cfg, up=up)
    corr = _matched_filter_norm(x, template.astype(np.float64))
    med = np.median(corr)
    mad = np.median(np.abs(corr - med)) * 1.4826
    thr = max(med + mad_k * mad, min_height)
    # 最小间距只需避免同一 chirp 的重复峰；leader 簇内间隔 150ms 必须能分开
    peaks, _ = signal.find_peaks(corr, height=thr,
                                 distance=max(int(1.5 * cfg.dur_ms / 1000 * sr), 1))
    return peaks / sr


def detect_markers(x: np.ndarray, sr: int, cfg: ChirpConfig) -> list[tuple[float, str]]:
    """检测上扫+下扫两组标记，返回按时间排序的 (时刻, 方向) 列表。"""
    xb = _bandpass(x.astype(np.float64), sr, cfg)
    ups = [(t, "up") for t in detect_chirps(xb, sr, cfg, up=True, _bandpassed=True)]
    downs = [(t, "down") for t in detect_chirps(xb, sr, cfg, up=False, _bandpassed=True)]
    return sorted(ups + downs)


# ---------------------------------------------------------------- leader 簇

def make_leader(sr: int, cfg: ChirpConfig, lcfg: LeaderConfig | None = None) -> np.ndarray:
    """生成 leader：n 个 chirp 等间隔紧密排列（沿用正文联段与交替扫频）。"""
    lcfg = lcfg or LeaderConfig()
    up, down = make_chirp(sr, cfg, True), make_chirp(sr, cfg, False)
    gap = int(round(lcfg.gap_ms / 1000 * sr))
    n = (lcfg.n_chirps - 1) * gap + len(up)
    out = np.zeros(n, dtype=np.float32)
    for i in range(lcfg.n_chirps):
        c = up if i % 2 == 0 else down
        out[i * gap:i * gap + len(c)] += c
    return out


def find_leader_clusters(det_peaks: list[tuple[float, str]], cfg: ChirpConfig,
                         lcfg: LeaderConfig | None = None,
                         max_gap_s: float = 1.0) -> list[tuple[float, float]]:
    """在检测峰中找"间距紧密的 chirp 簇"（leader）。

    正文标记间隔 5s，leader 簇内间隔 150ms，用 max_gap_s 聚类即可区分。
    返回 [(簇首 chirp 起点, 簇末 chirp 终点)]，按时间排序。
    """
    lcfg = lcfg or LeaderConfig()
    min_n = max(4, lcfg.n_chirps - 2)  # 容忍少量漏检
    if not det_peaks:
        return []
    clusters, cur = [], [det_peaks[0]]
    for p in det_peaks[1:]:
        if p[0] - cur[-1][0] < max_gap_s:
            cur.append(p)
        else:
            clusters.append(cur)
            cur = [p]
    clusters.append(cur)
    chirp_dur = cfg.dur_ms / 1000.0
    return [(c[0][0], c[-1][0] + chirp_dur) for c in clusters if len(c) >= min_n]


def strip_leader_clusters(det_peaks: list[tuple[float, str]], cfg: ChirpConfig,
                          lcfg: LeaderConfig | None = None) -> list[tuple[float, str]]:
    """剔除属于 leader 簇的检测峰（网格匹配只应看到正文标记）。"""
    leaders = find_leader_clusters(det_peaks, cfg, lcfg)
    if not leaders:
        return det_peaks
    return [(t, d) for t, d in det_peaks
            if not any(ls - 0.1 <= t <= le + 0.1 for ls, le in leaders)]
