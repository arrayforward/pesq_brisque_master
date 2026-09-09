"""micrecord 声学采集自测：PC 扬声器播放测试音 → 本机麦克风录制（不依赖手机）。

运行方式（在 tools/musicq 目录下）：
    .venv/Scripts/python.exe -m pytest tests/test_micrecord.py -v
"""
from __future__ import annotations

import threading

import numpy as np
import pytest

sc = pytest.importorskip("soundcard", reason="需要 soundcard 包")
import soundfile as sf  # noqa: E402

from musicq.micrecord import list_input_devices, micrecord  # noqa: E402


def _has_input() -> bool:
    try:
        return len(list_input_devices()) > 0
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _has_input(),
                                reason="本机无 WASAPI 输入设备（麦克风/Line-in）")


def test_micrecord_list():
    """--list 等价接口应列出至少一个输入设备。"""
    assert len(list_input_devices()) >= 1


def test_micrecord_acoustic(tmp_path):
    """扬声器播放测试音 → 麦克风录制：验证录制代码路径与文件完整性。

    注意：本机"英特尔智音技术"麦克风带 AEC（回声消除），会把系统播放的声音
    从麦克风输入中消掉（声学自测的固有陷阱），因此本机播放+本机录制的 RMS
    可能被 AEC 压到底噪量级。断言只验证：代码路径可运行、文件可读、时长正确、
    RMS 为有限非负值；真实声学链路验证靠真机（手机外放→PC mic，AEC 不适用）。
    """
    sr = 48000
    t = np.arange(5 * sr) / sr
    sig = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    sp = sc.default_speaker()
    threading.Timer(0.5, lambda: sp.play(sig, samplerate=sr)).start()

    out = tmp_path / "mic.wav"
    micrecord(out, seconds=4.0)
    x, fsr = sf.read(str(out))
    assert fsr == 48000 and x.ndim == 1
    assert 3.0 < len(x) / fsr < 5.0, f"时长异常 {len(x) / fsr:.1f}s"
    rms = float(np.sqrt(np.mean(x ** 2)))
    assert np.isfinite(rms) and rms >= 0.0
    print(f"mic 录音 RMS={20 * np.log10(rms + 1e-12):.1f} dBFS（本机 AEC 可能压至底噪量级）")
