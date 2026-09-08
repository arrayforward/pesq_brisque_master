"""批量加工测试音频：解码音源 → 重采样 48k 立体声 → 预埋 leader + 叠加 chirp 导频。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal

from .chirp import ChirpConfig, LeaderConfig, make_chirp, make_leader, marker_times

TARGET_SR = 48000
MARKERS_VERSION = 3  # markers.json 格式版本：v2 起含 leader/song_id；v3 起周期性 leader


def load_audio(path: Path, sr: int = TARGET_SR) -> tuple[np.ndarray, int]:
    """读入音频（mp3/wav/flac），统一为重采样后的 48kHz 立体声 float32。"""
    data, in_sr = sf.read(str(path), always_2d=True)
    data = data.astype(np.float64)
    if in_sr != sr:
        g = np.gcd(in_sr, sr)
        data = signal.resample_poly(data, sr // g, in_sr // g, axis=0)
    if data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)
    elif data.shape[1] > 2:
        data = data[:, :2]
    return data.astype(np.float32), sr


def overlay_markers(audio: np.ndarray, sr: int, cfg: ChirpConfig) -> tuple[np.ndarray, list[dict]]:
    """在音频上叠加 chirp 标记序列，返回 (叠加后音频, 标记时刻表)。"""
    out = audio.copy()
    total_s = len(audio) / sr
    markers = marker_times(total_s, cfg)
    chirps = {"up": make_chirp(sr, cfg, up=True), "down": make_chirp(sr, cfg, up=False)}
    for m in markers:
        c = chirps[m["direction"]]
        s0 = int(round(m["time_s"] * sr))
        out[s0:s0 + len(c)] += c[:, None]
    return out, markers


def build_test_audio(audio: np.ndarray, sr: int, cfg: ChirpConfig,
                     lcfg: LeaderConfig | None = None
                     ) -> tuple[np.ndarray, list[dict], dict]:
    """生成测试音频：歌头预埋 leader 簇，正文叠加常规标记 + 周期性 leader 簇。

    正文常规标记与任一 leader 簇时间过近（<1s）时让位跳过；标记方向按最终
    序号重新交替分配（保证奇偶交叉校验成立）。
    返回 (完整测试音频, 正文标记列表[含 leader 偏移], leader 信息 dict
    （含 leaders 全部簇位置）)。
    """
    lcfg = lcfg or LeaderConfig()
    leader = make_leader(sr, cfg, lcfg)
    leader_s = len(leader) / sr
    offset_s = leader_s + lcfg.gap_after_s
    total_s = len(audio) / sr + offset_s

    # 1) leader 簇时刻表：歌头 + 正文每 interval_s 一个
    leaders = [{"start_s": 0.0, "end_s": round(leader_s, 6)}]
    if lcfg.interval_s > 0:
        t = offset_s + lcfg.interval_s
        while t + leader_s + 0.5 < total_s:
            leaders.append({"start_s": round(t, 6), "end_s": round(t + leader_s, 6)})
            t += lcfg.interval_s

    # 2) 正文标记：与 leader 簇过近的让位；方向按最终序号交替
    body = audio.copy()
    chirps = {"up": make_chirp(sr, cfg, up=True), "down": make_chirp(sr, cfg, up=False)}
    markers = []
    for m in marker_times(len(audio) / sr, cfg):
        tm = m["time_s"] + offset_s
        if any(l["start_s"] - 1.0 < tm < l["end_s"] + 1.0 for l in leaders[1:]):
            continue  # 让位给 leader 簇，避免检测混淆
        m["index"] = len(markers)
        m["direction"] = "up" if m["index"] % 2 == 0 else "down"
        m["time_s"] = round(tm, 6)
        c = chirps[m["direction"]]
        s0 = int(round((tm - offset_s) * sr))
        body[s0:s0 + len(c)] += c[:, None]
        markers.append(m)

    # 3) 拼装：歌头 leader + 间隙 + 正文；周期性 leader 叠加到正文上
    gap = np.zeros((int(round(lcfg.gap_after_s * sr)), body.shape[1]), np.float32)
    out = np.concatenate([np.tile(leader[:, None], (1, body.shape[1])), gap, body])
    for l in leaders[1:]:
        s0 = int(round(l["start_s"] * sr))
        out[s0:s0 + len(leader)] += np.tile(leader[:, None], (1, out.shape[1]))
    # 防止叠加后削波
    peak = np.max(np.abs(out))
    if peak > 0.99:
        out *= 0.99 / peak
    leader_info = {"start_s": 0.0, "end_s": round(leader_s, 6), **lcfg.to_dict(),
                   "leaders": leaders}
    return out.astype(np.float32), markers, leader_info


def process_file(src: Path, out_dir: Path, cfg: ChirpConfig,
                 lcfg: LeaderConfig | None = None) -> Path | None:
    """加工单个音源文件；输出已是新格式（version>=2）则跳过。返回测试 wav 路径或 None。"""
    name = src.stem
    wav_out = out_dir / f"{name}_test.wav"
    json_out = out_dir / f"{name}_markers.json"
    if wav_out.exists() and json_out.exists():
        try:
            old = json.loads(json_out.read_text(encoding="utf-8"))
            if old.get("version", 1) >= MARKERS_VERSION:
                print(f"[跳过] 已存在: {wav_out.name}")
                return None
        except Exception:
            pass  # 旧格式/损坏 → 重新生成
    audio, sr = load_audio(src)
    audio, markers, leader_info = build_test_audio(audio, sr, cfg, lcfg)
    sf.write(str(wav_out), audio, sr, subtype="PCM_16")
    meta = {"version": MARKERS_VERSION, "song_id": name, "source": src.name,
            "sr": sr, "duration_s": round(len(audio) / sr, 6),
            "chirp": cfg.to_dict(), "leader": leader_info, "markers": markers}
    json_out.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[完成] {wav_out.name}（leader {leader_info['end_s']:.2f}s + {len(markers)} 个标记）")
    return wav_out


def generate(input_path: Path, out_dir: Path, cfg: ChirpConfig,
             lcfg: LeaderConfig | None = None) -> list[Path]:
    """input_path 可为单个音频文件或目录（批量）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    exts = {".mp3", ".wav", ".flac", ".ogg", ".m4a"}
    if input_path.is_dir():
        sources = sorted(p for p in input_path.iterdir() if p.suffix.lower() in exts)
    else:
        sources = [input_path]
    if not sources:
        raise SystemExit(f"未找到音频文件: {input_path}")
    done = []
    for src in sources:
        try:
            r = process_file(src, out_dir, cfg, lcfg)
            if r is not None:
                done.append(r)
        except Exception as e:  # 单个文件失败不阻塞批量
            print(f"[失败] {src.name}: {e}")
    print(f"共 {len(sources)} 个音源，新生成 {len(done)} 个测试音频 → {out_dir}")
    return done
