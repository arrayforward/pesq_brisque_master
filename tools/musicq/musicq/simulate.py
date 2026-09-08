"""合成劣化：模拟播放器 TSM 造成的非均匀时间形变 +  codec/噪声/延迟，用于无设备自测。"""
from __future__ import annotations

from fractions import Fraction

import numpy as np
from scipy import signal


def synthesize_degradation(audio: np.ndarray, sr: int, marker_times_s: list[float],
                           seed: int = 0,
                           n_stretch: tuple[int, int] = (3, 6),
                           rate_range: tuple[float, float] = (0.97, 1.03),
                           lpf_hz: float = 15000.0,
                           noise_dbfs: float = -40.0,
                           delay_s: float | None = None,
                           extra_times: list[float] | None = None) -> dict:
    """对测试音频合成劣化。

    伸缩段边界取在相邻标记的中点（保证每个标记区间速率恒定、chirp 不骑边界，便于验证）。
    extra_times: 额外需要真值位置的参考时刻（如周期性 leader 起点）。
    返回 dict：deg 音频、各标记的真值劣化时刻、各标记区间的真值速率比、固定延迟、
    extra 时刻真值位置（true_extra_times）。
    """
    rng = np.random.default_rng(seed)
    mt = np.array(marker_times_s)
    n_seg = int(rng.integers(n_stretch[0], n_stretch[1] + 1))
    # 在相邻标记的中点处切段（避免 chirp 正好骑在伸缩边界上被重采样边界瞬态扭曲），
    # 首尾边界固定在信号两端
    cut_idx = np.sort(rng.choice(np.arange(0, len(mt) - 1), size=min(n_seg, len(mt) - 1),
                                 replace=False))
    bounds = [0.0] + [float((mt[i] + mt[i + 1]) / 2) for i in cut_idx] + [len(audio) / sr]
    pieces = []
    true_pos = {0.0: 0.0}          # 参考时刻 -> 劣化时刻（起点对齐）
    true_extra = {}                # extra_times 的真值位置
    interval_rates = {}            # 标记区间 (i,i+1) -> 真值速率比
    cursor = 0.0                   # 劣化时间轴游标
    for bi in range(len(bounds) - 1):
        a, b = bounds[bi], bounds[bi + 1]
        r = float(rng.uniform(*rate_range))
        seg = audio[int(round(a * sr)):int(round(b * sr))]
        if len(seg) > 0:
            f = Fraction(r).limit_denominator(2000)
            seg = signal.resample_poly(seg, f.numerator, f.denominator, axis=0)
        pieces.append(seg)
        # 记录落入本段的标记/额外时刻的真值位置与区间速率
        for i, t in enumerate(mt):
            if a <= t < b:
                true_pos[float(t)] = cursor + (t - a) * r
                if i + 1 < len(mt) and mt[i + 1] <= b:
                    interval_rates[i] = r
        for t in (extra_times or []):
            if a <= t < b:
                true_extra[float(t)] = cursor + (t - a) * r
        cursor += len(seg) / sr
    deg = np.concatenate(pieces, axis=0)

    # 15kHz 低通（模拟编码器带宽限制）
    sos = signal.butter(8, lpf_hz, btype="low", fs=sr, output="sos")
    deg = signal.sosfiltfilt(sos, deg, axis=0)

    # 加性噪声
    noise_amp = 10.0 ** (noise_dbfs / 20.0)
    deg = deg + rng.standard_normal(deg.shape).astype(np.float32) * noise_amp

    # 随机固定延迟
    if delay_s is None:
        delay_s = float(rng.uniform(0.05, 0.5))
    n_d = int(round(delay_s * sr))
    deg = np.concatenate([np.zeros((n_d,) + deg.shape[1:], np.float32), deg], axis=0)
    true_pos = {t: p + delay_s for t, p in true_pos.items()}
    true_extra = {t: p + delay_s for t, p in true_extra.items()}

    return {"deg": deg.astype(np.float32), "sr": sr,
            "true_deg_times": true_pos, "interval_rates": interval_rates,
            "delay_s": delay_s, "true_extra_times": true_extra}


# 劣化档位：(速率比范围, 低通Hz, 噪声dBFS)
STRENGTHS = {
    "copy":     None,  # 原样拷贝（仅转单声道，模拟采集通道）
    "mild":     ((0.997, 1.003), 18000.0, -45.0),
    "standard": ((0.97, 1.03), 15000.0, -40.0),
    "strong":   ((0.95, 1.05), 12000.0, -30.0),
}


def run_synthetic_check(src, workdir, seed: int = 0, strength: str = "standard",
                        duration_s: float = 60.0) -> dict:
    """全链路合成自测：生成测试音频 → 合成劣化 → 对齐 → 评分 → 精度统计。"""
    import json
    from pathlib import Path

    import soundfile as sf

    from .chirp import ChirpConfig
    from .generate import load_audio, overlay_markers
    from .align import align
    from .report import score_dir

    src, workdir = Path(src), Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    cfg = ChirpConfig()

    # 1) 生成带 chirp 的测试音频（截取前 duration_s 秒，控制自测耗时）
    audio, sr = load_audio(src)
    audio = audio[:int(duration_s * sr)]
    audio, markers = overlay_markers(audio, sr, cfg)
    ref_wav = workdir / "ref_test.wav"
    sf.write(str(ref_wav), audio, sr, subtype="PCM_16")
    markers_json = workdir / "ref_markers.json"
    markers_json.write_text(json.dumps(
        {"source": str(src), "sr": sr, "chirp": cfg.to_dict(), "markers": markers},
        ensure_ascii=False, indent=2), encoding="utf-8")
    mt = [m["time_s"] for m in markers]

    # 2) 合成劣化（deg 转单声道，模拟 scrcpy 采集通道）
    if strength == "copy":
        deg = audio.mean(axis=1)
        truth = {"true_deg_times": {t: t for t in mt},
                 "interval_rates": {i: 1.0 for i in range(len(mt) - 1)},
                 "delay_s": 0.0}
    else:
        rate_range, lpf_hz, noise_dbfs = STRENGTHS[strength]
        truth = synthesize_degradation(audio, sr, mt, seed=seed,
                                       rate_range=rate_range, lpf_hz=lpf_hz,
                                       noise_dbfs=noise_dbfs)
        deg = truth["deg"].mean(axis=1)
    deg_wav = workdir / "deg.wav"
    sf.write(str(deg_wav), deg, sr, subtype="PCM_16")

    # 3) 对齐 + 评分
    al = align(ref_wav, markers_json, deg_wav, workdir / "aligned")
    rep = score_dir(workdir / "aligned")

    # 4) 精度统计：速率比误差 / 固定延迟误差
    # 固定延迟估计 = 各标记 (检测时刻-真值时刻) 的中位数（漂移已由真值去除，
    # 个别标记受强高频音乐内容影响会有 ~1ms 抖动，中位数对此鲁棒）
    rate_errors = []
    for seg in al["segments"]:
        i0, i1 = seg["ref_marker_range"]
        # 真值速率直接由标记真值位置计算（段跨伸缩边界时也精确）
        t0, t1 = seg["ref_start_s"], seg["ref_end_s"]
        if t0 in truth["true_deg_times"] and t1 in truth["true_deg_times"]:
            r_true = ((truth["true_deg_times"][t1] - truth["true_deg_times"][t0])
                      / (t1 - t0))
            rate_errors.append(abs(seg["rate_ratio"] - r_true))
    det_errs = [(m["deg_time_s"] - m["ref_time_s"]) -
                (truth["true_deg_times"][m["ref_time_s"]] - m["ref_time_s"])
                for m in al["matches"] if m["ref_time_s"] in truth["true_deg_times"]]
    vis = [r["visqol"] for r in rep["rows"] if r.get("visqol") is not None]
    snr = [r["snr_db"] for r in rep["rows"]]
    summary = {
        "strength": strength, "n_segments": len(al["segments"]),
        "max_rate_error": max(rate_errors) if rate_errors else None,
        "mean_rate_error": sum(rate_errors) / len(rate_errors) if rate_errors else None,
        "delay_s_true": truth["delay_s"],
        "delay_est_error_s": float(np.median(det_errs)) if det_errs else None,
        "delay_marker_p90_s": float(np.percentile(np.abs(det_errs), 90)) if det_errs else None,
        "visqol_median": float(np.median(vis)) if vis else None,
        "snr_median_db": float(np.median(snr)) if snr else None,
    }
    return {"summary": summary, "alignment": al, "report": rep}
