"""长录音自动识别 + 自动打分（mq autoscore）。

流程：检测正文 chirp 标记 → 全局网格段发现（对候选歌曲反复做网格匹配+内容验证，
每轮锁定一段"同一首歌的连续标记段"，移除后重复，直到没有可识别段）→
逐段 align + score → 按歌曲聚合 + 总体聚合。

设计说明：v2 方案"先检测 leader 簇再切分"在强噪/声学链路下不可靠——叠加在
音乐上的 leader 与簇模板的相关值会被音乐稀释（~0.05），而正文单 chirp 对簇模板
的部分匹配（~0.34）反而更高，局部方法无法区分。正文标记的 5s 等间隔+奇偶交替
是更强的结构先验：假峰随机散布，无法构成一致网格，网格匹配天然剔除假峰；
发现段后可由标记序号回推段界，无需依赖 leader 检出。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import soundfile as sf

from .chirp import ChirpConfig, LeaderConfig, detect_markers
from .align import align, load_for_align, match_grid, _to_mono
from .generate import TARGET_SR
from .report import compute_rows, write_report

IDENTIFY_MIN_SCORE = 0.3   # 歌曲识别内容验证分下限（正确匹配通常 >0.5，错配 <0.1）
MIN_INSTANCE_MARKERS = 3   # 段发现的最少连续标记数


def load_ref_db(ref_dir: Path) -> list[dict]:
    """扫描 ref-dir 下全部 *_markers.json + *_test.wav，返回候选歌曲列表。"""
    cands = []
    for mj in sorted(Path(ref_dir).glob("*_markers.json")):
        wav = mj.with_name(mj.name.replace("_markers.json", "_test.wav"))
        if not wav.exists():
            continue
        meta = json.loads(mj.read_text(encoding="utf-8"))
        cands.append({"song_id": meta.get("song_id", mj.name[:-len("_markers.json")]),
                      "meta": meta, "markers_json": mj, "wav": wav})
    if not cands:
        raise SystemExit(f"{ref_dir} 下没有找到 *_markers.json 候选歌曲")
    return cands


def discover_instances(rec_m: np.ndarray, sr: int, det_peaks: list[tuple[float, str]],
                       cands: list[dict], ref_cache: dict) -> list[dict]:
    """全局网格段发现：反复对剩余检测峰做网格匹配+内容验证，锁定歌曲段。

    返回按录音时间排序的实例列表，每个实例含：候选歌曲、匹配映射
    {marker序号: 录音时刻}、录音起止时间（含余量）。
    """
    remaining = list(det_peaks)
    found: list[dict] = []
    while True:
        best = None  # (score, cand, matches)
        for c in cands:
            cfg_c = ChirpConfig(**c["meta"]["chirp"])
            if c["song_id"] not in ref_cache:
                ref_cache[c["song_id"]] = _to_mono(
                    load_for_align(c["wav"], sr)).astype(np.float64)
            try:
                matches, score = match_grid(c["meta"]["markers"], remaining, cfg_c,
                                            ref_m=ref_cache[c["song_id"]],
                                            deg_m=rec_m, sr=sr)
            except RuntimeError:
                continue
            if best is None or score > best[0]:
                best = (score, c, matches)
        if best is None or best[0] < IDENTIFY_MIN_SCORE \
                or len(best[2]) < MIN_INSTANCE_MARKERS:
            break
        score, cand, matches = best
        ref_t = [m["time_s"] for m in cand["meta"]["markers"]]

        # 按 marker 序号连续性切段（序号回退/跳号 → 同歌多次循环分成多段）
        keys = sorted(matches)
        groups: list[list[int]] = [[keys[0]]]
        for a, b in zip(keys, keys[1:]):
            if b == a + 1:
                groups[-1].append(b)
            else:
                groups.append([b])
        matched_times = set()
        for g in groups:
            if len(g) < MIN_INSTANCE_MARKERS:
                continue
            i0, i1 = g[0], g[-1]
            t0, t1 = matches[i0], matches[i1]
            # 段录音范围：段首 marker 回推到 marker 0 的推算位置（含歌头余量），
            # 段尾 marker 向后留一个标记间隔
            rec_start = max(0.0, t0 - (ref_t[i0] - ref_t[0]) - 1.0)
            rec_end = min(len(rec_m) / sr, t1 + 6.0)
            found.append({"cand": cand, "matches": {i: matches[i] for i in g},
                          "score": score, "first_marker": i0,
                          "rec_start_s": rec_start, "rec_end_s": rec_end})
            matched_times.update(matches[i] for i in g)
        # 移除已匹配的峰（50ms 容差），继续发现下一段
        remaining = [(t, d) for t, d in remaining
                     if not any(abs(t - mt) < 0.05 for mt in matched_times)]

    found.sort(key=lambda f: f["rec_start_s"])
    return found


def _safe_name(s: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", s)


def autoscore(rec_wav: Path, ref_dir: Path, out_dir: Path,
              cfg: ChirpConfig | None = None, lcfg: LeaderConfig | None = None,
              peaq_bin: Path | None = None) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 检测用 chirp 参数：显式指定优先，否则取参考目录第一个候选歌曲的
    # （测试音频可能是 acoustic 等非默认预设，用默认参数检测会全频段错位）
    cands = load_ref_db(ref_dir)
    if cfg is None:
        cfg = ChirpConfig(**cands[0]["meta"]["chirp"])
    lcfg = lcfg or LeaderConfig()

    rec = load_for_align(rec_wav, TARGET_SR)
    sr = TARGET_SR
    rec_m = _to_mono(rec).astype(np.float64)
    total_s = len(rec) / sr

    # 1) 全录音正文标记检测（低阈值，假峰由网格匹配剔除）
    det = detect_markers(rec_m, sr, cfg)
    print(f"录音 {total_s:.1f}s，检测到 {len(det)} 个 chirp 峰")

    # 2) 全局网格段发现 → 逐段 align + score
    ref_cache: dict[str, np.ndarray] = {}
    found = discover_instances(rec_m, sr, det, cands, ref_cache)
    print(f"发现 {len(found)} 个歌曲实例段")
    if not found:
        raise SystemExit(
            "未能识别出任何歌曲实例段。\n"
            "请确认：① 参考目录（--ref-dir）与录音内容匹配；"
            "② 录音时长大于一个标记间隔（默认 5s）；"
            "③ 采集链未滤掉 chirp 频段。")

    loops: dict[str, int] = {}
    instances: list[dict] = []
    all_rows: list[dict] = []
    for k, f in enumerate(found):
        cand = f["cand"]
        song_id = cand["song_id"]
        ls, end = f["rec_start_s"], f["rec_end_s"]
        a, b = int(ls * sr), int(end * sr)
        loops[song_id] = loops.get(song_id, 0) + 1
        loop_no = loops[song_id]
        print(f"实例 {k}: 录音 {ls:.1f}s ~ {end:.1f}s →「{song_id}」"
              f"（第 {loop_no} 次循环，起始标记 #{f['first_marker']}，"
              f"验证分 {f['score']:.3f}）")

        inst_dir = out_dir / f"inst_{k:02d}_{_safe_name(song_id)}"
        inst_dir.mkdir(parents=True, exist_ok=True)
        inst_wav = inst_dir / "instance.wav"
        sf.write(str(inst_wav), rec[a:b], sr, subtype="PCM_16")
        al = align(cand["wav"], cand["markers_json"], inst_wav,
                   inst_dir / "aligned")
        rows = compute_rows(al, inst_dir / "aligned", peaq_bin=peaq_bin)
        for r in rows:
            r.update({"instance": k, "song_id": song_id, "loop": loop_no,
                      "rec_start_s": round(ls, 3), "rec_end_s": round(end, 3),
                      "rec_time_s": round(ls + r["ref_start_s"], 3)})
        all_rows += rows
        instances.append({"instance": k, "song_id": song_id, "loop": loop_no,
                          "first_marker": int(f["first_marker"]),
                          "rec_start_s": round(ls, 3), "rec_end_s": round(end, 3),
                          "identify_score": round(f["score"], 4),
                          "align_dir": str(inst_dir / "aligned")})

    # 3) 汇总报告（按歌曲聚合 + 总体聚合）
    rep = write_report(all_rows, out_dir, group_key="song_id",
                       title="musicq autoscore quality curve")
    (out_dir / "autoscore.json").write_text(
        json.dumps({"rec_wav": str(rec_wav), "instances": instances},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    return {"instances": instances, "rows": all_rows, **rep}
