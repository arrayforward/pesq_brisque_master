"""btrecord 回环录制自测（不依赖手机/蓝牙）：PC 本地播放 + WASAPI loopback 录制。

运行方式（在 tools/musicq 目录下）：
    .venv/Scripts/python.exe -m pytest tests/test_btrecord.py -v
"""
from __future__ import annotations

import threading

import numpy as np
import pytest

sc = pytest.importorskip("soundcard", reason="需要 soundcard 包（WASAPI loopback）")
from scipy import signal  # noqa: E402

from musicq.btrecord import btrecord  # noqa: E402


def _has_loopback() -> bool:
    try:
        return any(m.isloopback for m in sc.all_microphones(include_loopback=True))
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _has_loopback(),
                                reason="本机无 WASAPI loopback 录制设备")


def test_btrecord_loopback(tmp_path):
    """本地播放测试音 → btrecord 回环录制：非静音且与播放内容强相关。"""
    sr = 48000
    t = np.arange(6 * sr) / sr
    sig = (0.3 * np.sin(2 * np.pi * 440 * t)
           + 0.2 * np.sin(2 * np.pi * 3000 * t)).astype(np.float32)
    sp = sc.default_speaker()
    threading.Timer(0.5, lambda: sp.play(sig, samplerate=sr)).start()
    out = tmp_path / "bt.wav"
    btrecord(out, seconds=4.0, use_sink=False)
    import soundfile as sf
    x, fsr = sf.read(str(out))
    assert fsr == 48000 and x.ndim == 1
    rms = float(np.sqrt(np.mean(x ** 2)))
    assert rms > 1e-3, f"录制接近静音 RMS={rms}"
    corr = signal.fftconvolve(x, sig[::-1], mode="full")
    k = int(np.argmax(np.abs(corr)))
    q = float(np.abs(corr[k]) / (np.linalg.norm(sig) * np.linalg.norm(x)))
    assert q > 0.7, f"录制内容与播放内容相关性过低: {q:.3f}"


def test_btrecord_sink_unavailable_hint(tmp_path):
    """本机无 A2DP sink 端点时，use_sink 应给出指引并以退出码 2 失败。"""
    from musicq.btrecord import open_a2dp_sink
    if open_a2dp_sink() is not None:
        pytest.skip("本机支持 A2DP sink，无法验证失败路径")
    with pytest.raises(SystemExit) as e:
        btrecord(tmp_path / "x.wav", seconds=1.0, use_sink=True)
    assert e.value.code == 2
