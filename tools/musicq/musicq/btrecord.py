"""蓝牙 A2DP 采集通道：PC 模拟蓝牙音响（A2DP sink），手机蓝牙推送音频，PC 回环录音。

链路：手机蓝牙 → PC（A2DP sink, AudioPlaybackConnection）→ 渲染到输出设备
      → WASAPI loopback 录制 → 48kHz 单声道 wav（供 autoscore 打分）。

两个命令配合两步交互：
- `btsink`：打开 sink（open+start）并保持运行，JSON 行输出状态变化，直到进程被杀；
- `btrecord`：录制回环（--no-sink 时只录不开 sink，配合 btsink 进程使用）。

依赖系统/驱动的 A2DP sink 支持（Windows 10 2004+ 且蓝牙驱动发布 sink 端点）；
不支持时打印明确指引并退出（exit 2），不影响 scrcpy 通道。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

from .generate import TARGET_SR

SINK_HELP = """\
[错误] 本机未检测到可用的蓝牙 A2DP sink 端点（AudioPlaybackConnection 枚举为空）。
A2DP sink 依赖蓝牙驱动支持：
  - Windows 10 2004+ / Windows 11，且蓝牙驱动需发布 A2DP sink 渲染接口；
  - 实测本机 Intel Wireless Bluetooth（Win11 build 26200）不发布该端点，不可用；
  - 已知可用的环境：部分 Realtek/Broadcom 蓝牙、部分 OEM 定制驱动
    （Microsoft Store 的 "Bluetooth Audio Receiver" 应用基于同一 API，可作对照）。
请改用 scrcpy 采集通道，或确认驱动支持后重试。\
"""


def open_a2dp_sink():
    """枚举并打开第一个 A2DP sink 端点；不可用返回 None。"""
    from winsdk.windows.media.audio import AudioPlaybackConnection
    from winsdk.windows.devices.enumeration import DeviceInformation

    async def _open():
        sel = AudioPlaybackConnection.get_device_selector()
        devs = await DeviceInformation.find_all_async(sel, [])
        if len(devs) == 0:
            return None, 0
        apc = AudioPlaybackConnection.try_create_from_id(devs[0].id)
        if apc is None:
            return None, len(devs)
        await apc.open_async()
        await apc.start_async()  # open 只建通道，start 才开始监听远端连接
        return apc, len(devs)

    apc, n = asyncio.run(_open())
    if apc is None:
        print(f"检测到 {n} 个 sink 端点但无法打开" if n else "", file=sys.stderr)
        return None
    return apc


def _pick_loopback(device_hint: str | None):
    """选回环录制设备：--device 模糊匹配；否则默认输出设备的 loopback。"""
    import soundcard as sc
    mics = sc.all_microphones(include_loopback=True)
    loopbacks = [m for m in mics if m.isloopback]
    if not loopbacks:
        raise SystemExit("[错误] 未找到任何 WASAPI 回环录制设备")
    if device_hint:
        for m in loopbacks:
            if device_hint.lower() in m.name.lower():
                return m
        names = "、".join(m.name for m in loopbacks)
        raise SystemExit(f"[错误] 回环设备未匹配到 '{device_hint}'，可选: {names}")
    # 默认输出设备的 loopback
    try:
        sp = sc.default_speaker()
        for m in loopbacks:
            if m.name == sp.name or sp.name in m.name:
                return m
    except Exception:
        pass
    return loopbacks[0]


def btsink() -> None:
    """打开 A2DP sink 并保持运行，JSON 行输出状态变化，直到进程被杀。

    状态行：{"state":"waiting"}（已监听等待手机连接）
            {"state":"opened"}（手机已连接）/ {"state":"closed"}（断开）
    供 GUI 两步流程（先开 sink 等连接，再 btrecord --no-sink 录制）使用。
    """
    from winsdk.windows.media.audio import AudioPlaybackConnection
    from winsdk.windows.devices.enumeration import DeviceInformation

    def _emit(state: str):
        print(json.dumps({"state": state}), flush=True)

    async def _run():
        sel = AudioPlaybackConnection.get_device_selector()
        devs = await DeviceInformation.find_all_async(sel, [])
        if len(devs) == 0:
            print(SINK_HELP, file=sys.stderr)
            raise SystemExit(2)
        apc = AudioPlaybackConnection.try_create_from_id(devs[0].id)
        if apc is None:
            print(SINK_HELP, file=sys.stderr)
            raise SystemExit(2)

        def on_state(sender, _args):
            # AudioPlaybackConnectionState: 0=CLOSED 1=OPENED
            _emit("opened" if int(sender.state) == 1 else "closed")

        apc.add_state_changed(on_state)
        await apc.open_async()
        await apc.start_async()  # open 只建通道，start 才开始监听远端连接
        _emit("waiting")
        print("A2DP sink 已打开，PC 对外呈现为蓝牙音响，等待手机连接…", flush=True)
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            apc.close()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


def btrecord(out_wav: Path, seconds: float, device: str | None = None,
             use_sink: bool = True) -> Path:
    """录制 N 秒回环音频写 48kHz 单声道 wav。

    use_sink=False（--no-sink）时只录不开 sink——适用于 sink 已由其他进程
    （如 btsink 命令）打开并保持的场景，或纯回环录制自测。
    """
    import soundcard as sc

    apc = None
    if use_sink:
        apc = open_a2dp_sink()
        if apc is None:
            print(SINK_HELP, file=sys.stderr)
            raise SystemExit(2)
        print("A2DP sink 已打开，PC 对外呈现为蓝牙音响。"
              "请在手机蓝牙设置中连接本电脑后开始播放。")

    mic = _pick_loopback(device)
    print(f"回环录制设备: {mic.name}，录制 {seconds:.0f}s → {out_wav}")
    n = int(round(seconds * TARGET_SR))
    # WASAPI 回环可直接按 48kHz 工程采样率录制（A2DP 44.1k 由系统重采样）
    data = mic.record(samplerate=TARGET_SR, numframes=n, channels=1)
    mono = data if data.ndim == 1 else data.mean(axis=1)
    mono = mono.astype(np.float32)

    peak = float(np.max(np.abs(mono))) if len(mono) else 0.0
    if peak > 0.99:
        mono *= 0.99 / peak
    sf.write(str(out_wav), mono, TARGET_SR, subtype="PCM_16")

    rms = float(np.sqrt(np.mean(mono ** 2))) if len(mono) else 0.0
    if rms < 1e-5:
        print("[警告] 录制结果接近全静音！手机未连接/未播放，或输出设备不对。", file=sys.stderr)
    else:
        print(f"录制完成 → {out_wav}（RMS={20 * np.log10(rms + 1e-12):.1f} dBFS）")

    if apc is not None:
        apc.close()
    return out_wav
