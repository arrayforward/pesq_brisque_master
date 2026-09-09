"""mic/line-in 声学采集通道：手机外放 → PC 麦克风，或耳机口 → PC Line-in 有线直连。

scrcpy 与蓝牙 A2DP 都不可用时的兜底通道。录制复用 btrecord 的分块写盘逻辑
（_record_to_wav：0.5s 分块 + 逐块 flush 保 header + 静音校验）。
"""
from __future__ import annotations

import sys
from pathlib import Path

from .btrecord import _record_to_wav
from .generate import TARGET_SR


def list_input_devices() -> list[str]:
    """列出全部 WASAPI 输入设备名（麦克风/Line-in/立体声混音），供 GUI 填充下拉框。"""
    import soundcard as sc
    return [m.name for m in sc.all_microphones(include_loopback=False)]


def _pick_input(device_hint: str | None):
    """选输入设备：--device 模糊匹配；否则默认输入设备。"""
    import soundcard as sc
    mics = sc.all_microphones(include_loopback=False)
    if not mics:
        raise SystemExit("[错误] 未找到任何 WASAPI 输入设备（麦克风/Line-in）")
    if device_hint:
        for m in mics:
            if device_hint.lower() in m.name.lower():
                return m
        names = "、".join(m.name for m in mics)
        raise SystemExit(f"[错误] 输入设备未匹配到 '{device_hint}'，可选: {names}")
    try:
        return sc.default_microphone()
    except Exception:
        return mics[0]


def micrecord(out_wav: Path, seconds: float, device: str | None = None) -> Path:
    """从输入设备录制 N 秒写 48kHz 单声道 wav。seconds<=0 = 不限时长直到被杀。"""
    mic = _pick_input(device)
    dur = f"{seconds:.0f}s" if seconds > 0 else "不限时长（直到停止）"
    print(f"输入录制设备: {mic.name}，录制 {dur} → {out_wav}")
    return _record_to_wav(mic, out_wav, seconds)
