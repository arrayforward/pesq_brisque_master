"""采集：scrcpy 录制系统播放输出 + ffmpeg 抽取音频。

scrcpy / ffmpeg 不在 PATH 时打印 winget 安装指引并优雅退出。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np


def _require(tool: str) -> str:
    path = shutil.which(tool)
    if path:
        return path
    print(f"[错误] 未找到 {tool}。请先安装：", file=sys.stderr)
    print(f"    winget install {tool}", file=sys.stderr)
    if tool == "scrcpy":
        print("  并确认 adb 可用、设备已开启 USB 调试（Android 11+ 支持播放捕获）。", file=sys.stderr)
    raise SystemExit(2)


def capture(out_mkv: Path, device: str | None = None) -> Path:
    """调用 scrcpy 录制设备音频输出（不投屏、不控制）。

    启动后由用户手动在设备上起播测试音频，Ctrl+C 结束录制。
    """
    scrcpy = _require("scrcpy")
    cmd = [scrcpy, "--no-video", "--no-control",
           "--audio-codec=flac", f"--record={out_mkv}"]
    if device:
        cmd += ["-s", device]
    print("即将开始录制。请在设备上手动起播测试音频，结束后按 Ctrl+C。")
    print("命令:", " ".join(cmd))
    try:
        subprocess.run(cmd, check=True)
    except KeyboardInterrupt:
        pass
    print(f"录制结束 → {out_mkv}")
    return out_mkv


def extract(in_mkv: Path, out_wav: Path, sr: int = 48000) -> Path:
    """用 ffmpeg 从录制文件抽出 48kHz 单声道 wav，并校验非全静音。"""
    ffmpeg = _require("ffmpeg")
    cmd = [ffmpeg, "-y", "-i", str(in_mkv), "-ac", "1", "-ar", str(sr), str(out_wav)]
    subprocess.run(cmd, check=True)
    import soundfile as sf
    data, _ = sf.read(str(out_wav))
    rms = float(np.sqrt(np.mean(data ** 2))) if len(data) else 0.0
    if rms < 1e-5:
        print("[警告] 抽取结果接近全静音！目标 App 可能禁止了回放采集，"
              "或采集通道不对，请检查后再跑后续流程。", file=sys.stderr)
    else:
        print(f"抽取完成 → {out_wav}（RMS={20 * np.log10(rms + 1e-12):.1f} dBFS）")
    return out_wav
