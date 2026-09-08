"""评分指标：ViSQOLAudio（主）+ 外部 PEAQ 二进制（可选）+ SNR/segSNR/THD+N（纯 numpy）。"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

_VISQOL_API = None
_VISQOL_TRIED = False


def _get_visqol():
    """懒加载 ViSQOLAudio（audio 模式 48kHz）。导入失败返回 None 并告警一次。"""
    global _VISQOL_API, _VISQOL_TRIED
    if _VISQOL_TRIED:
        return _VISQOL_API
    _VISQOL_TRIED = True
    try:
        from visqol import VisqolApi
        api = VisqolApi()
        api.create(mode="audio")
        _VISQOL_API = api
    except Exception as e:
        print(f"[警告] ViSQOL 不可用，跳过该指标: {e}", file=sys.stderr)
        _VISQOL_API = None
    return _VISQOL_API


def visqol_score(ref_wav: Path, deg_wav: Path) -> float | None:
    api = _get_visqol()
    if api is None:
        return None
    try:
        return float(api.measure(str(ref_wav), str(deg_wav)).moslqo)
    except Exception as e:
        print(f"[警告] ViSQOL 评分失败 {deg_wav.name}: {e}", file=sys.stderr)
        return None


def peaq_score(ref_wav: Path, deg_wav: Path, peaq_bin: Path) -> float | None:
    """调用外部 PEAQ 二进制，解析 ODG。二进制须支持 `peaq <ref.wav> <deg.wav>` 并在输出中给出 ODG。"""
    try:
        out = subprocess.run([str(peaq_bin), str(ref_wav), str(deg_wav)],
                             capture_output=True, text=True, timeout=600)
        text = out.stdout + out.stderr
        m = re.search(r"ODG[=:\s]+(-?\d+(?:\.\d+)?)", text)
        if m:
            return float(m.group(1))
        print(f"[警告] PEAQ 输出中未找到 ODG: {text[:200]}", file=sys.stderr)
    except Exception as e:
        print(f"[警告] PEAQ 调用失败: {e}", file=sys.stderr)
    return None


def _mono_pair(ref_wav: Path, deg_wav: Path) -> tuple[np.ndarray, np.ndarray, int]:
    ref, sr = sf.read(str(ref_wav), always_2d=True)
    deg, sr2 = sf.read(str(deg_wav), always_2d=True)
    assert sr == sr2, f"采样率不一致: {sr} vs {sr2}"
    n = min(len(ref), len(deg))
    return ref[:n].mean(axis=1), deg[:n].mean(axis=1), sr


def _gain_correct(ref: np.ndarray, deg: np.ndarray) -> np.ndarray:
    """最优增益校正：解析式最小二乘 g = <ref,deg>/<ref,ref>，返回 deg/g。

    采集链电平跟随设备媒体音量，与参考可差数十分贝；不归一化会把电平差
    全算进误差（实测手机采集低 26.5dB 时 SNR 从 64.75dB 误报为 0.42dB）。
    防御：g 非正/非有限/异常小（如 deg 全零、信号不相关）时不做校正。
    """
    denom = float(np.dot(ref, ref))
    if denom <= 1e-12:
        return deg
    g = float(np.dot(ref, deg)) / denom
    if not np.isfinite(g) or g <= 1e-3 or g > 1e3:
        return deg
    return deg / g


def snr_db(ref: np.ndarray, deg: np.ndarray) -> float:
    """全局 SNR（要求已对齐）。"""
    err = ref - deg
    return float(10 * np.log10(np.sum(ref ** 2) / (np.sum(err ** 2) + 1e-12) + 1e-12))


def segsnr_db(ref: np.ndarray, deg: np.ndarray, sr: int,
              frame_ms: float = 20.0) -> float:
    """分段 SNR：20ms 帧、50% 重叠、逐帧 dB 限幅 [-10, 35] 后取均值。"""
    fl = int(sr * frame_ms / 1000)
    hop = fl // 2
    vals = []
    for s in range(0, len(ref) - fl, hop):
        r, d = ref[s:s + fl], deg[s:s + fl]
        p_ref, p_err = np.sum(r ** 2), np.sum((r - d) ** 2)
        if p_ref < 1e-8:  # 静音帧不计
            continue
        vals.append(np.clip(10 * np.log10(p_ref / (p_err + 1e-12)), -10, 35))
    return float(np.mean(vals)) if vals else float("nan")


def thd_n_db(x: np.ndarray, sr: int, n_harm: int = 8) -> float:
    """THD+N：对主导谱线做基波+谐波最小二乘拟合，剩余视为噪声+失真。

    音乐信号下该值仅作 sanity 参考，不是严格意义的 THD+N。
    """
    n = len(x)
    sp = np.abs(np.fft.rfft(x * np.hanning(n)))
    freqs = np.fft.rfftfreq(n, 1 / sr)
    band = (freqs > 50) & (freqs < 2000)
    if not band.any():
        return float("nan")
    f0 = freqs[band][np.argmax(sp[band])]
    t = np.arange(n) / sr
    cols = []
    for k in range(1, n_harm + 1):
        fk = f0 * k
        if fk > sr * 0.45:
            break
        cols += [np.sin(2 * np.pi * fk * t), np.cos(2 * np.pi * fk * t)]
    if not cols:
        return float("nan")
    A = np.stack(cols, axis=1)
    coef, *_ = np.linalg.lstsq(A, x, rcond=None)
    resid = x - A @ coef
    return float(10 * np.log10(np.sum(resid ** 2) / (np.sum(x ** 2) + 1e-12) + 1e-12))


def compute_all(ref_wav: Path, deg_wav: Path, peaq_bin: Path | None = None) -> dict:
    """逐段指标汇总。visqol/peaq 不可用时对应字段为 None。"""
    ref, deg, sr = _mono_pair(ref_wav, deg_wav)
    deg = _gain_correct(ref, deg)  # 采集电平增益归一（THD+N 比例量纲不变，SNR 系必须）
    odg = None
    if peaq_bin is not None:
        if Path(peaq_bin).exists():
            odg = peaq_score(ref_wav, deg_wav, peaq_bin)
        else:
            print(f"[警告] PEAQ 二进制不存在，跳过: {peaq_bin}", file=sys.stderr)
    return {
        "visqol": visqol_score(ref_wav, deg_wav),
        "peaq_odg": odg,
        "snr_db": snr_db(ref, deg),
        "segsnr_db": segsnr_db(ref, deg, sr),
        "thd_n_db": thd_n_db(deg, sr),
    }
