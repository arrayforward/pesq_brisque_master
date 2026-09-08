"""长录音自动识别 + 自动打分（mq autoscore）。

流程：检测全部 chirp 峰 → 紧密簇找出各次播放的 leader → 按 leader 切实例 →
逐实例歌曲识别（网格匹配 + 内容验证，遍历候选歌曲取最高分）→ 复用 align/score 出报告。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import soundfile as sf

from .chirp import (ChirpConfig, LeaderConfig, _bandpass, detect_markers,
                    find_leader_clusters, strip_leader_clusters)
from .align import align, load_for_align, match_grid, _refine_marker, _to_mono
from .generate import TARGET_SR
from .report import compute_rows, write_report

IDENTIFY_MIN_SCORE = 0.3   # 歌曲识别内容验证分下限（正确匹配通常 >0.5，错配 <0.1）
MIN_INSTANCE_S = 5.0       # 过短的实例跳过


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


def identify_instance(inst_m: np.ndarray, sr: int, inst_peaks: list,
                      cands: list[dict], ref_cache: dict) -> tuple[float, dict] | None:
    """用网格匹配 + 内容验证逐一尝试候选歌曲，返回 (验证分, 候选) 或 None。"""
    best = None
    for c in cands:
        cfg_c = ChirpConfig(**c["meta"]["chirp"])
        if c["song_id"] not in ref_cache:
            ref_cache[c["song_id"]] = _to_mono(
                load_for_align(c["wav"], sr)).astype(np.float64)
        try:
            _, score = match_grid(c["meta"]["markers"], inst_peaks, cfg_c,
                                  ref_m=ref_cache[c["song_id"]],
                                  deg_m=inst_m, sr=sr)
        except RuntimeError:
            continue
        if best is None or score > best[0]:
            best = (score, c)
    return best


def _safe_name(s: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", s)


def autoscore(rec_wav: Path, ref_dir: Path, out_dir: Path,
              cfg: ChirpConfig | None = None, lcfg: LeaderConfig | None = None,
              peaq_bin: Path | None = None) -> dict:
    cfg = cfg or ChirpConfig()
    lcfg = lcfg or LeaderConfig()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rec = load_for_align(rec_wav, TARGET_SR)
    sr = TARGET_SR
    rec_m = _to_mono(rec).astype(np.float64)
    rec_bp = _bandpass(rec_m, sr, cfg)
    total_s = len(rec) / sr

    # 1) 全录音 chirp 检测 + leader 簇
    det = detect_markers(rec_m, sr, cfg)
    leaders = find_leader_clusters(det, cfg, lcfg)
    body_peaks = strip_leader_clusters(det, cfg, lcfg)
    print(f"录音 {total_s:.1f}s，检测到 {len(det)} 个 chirp 峰，"
          f"发现 {len(leaders)} 个 leader（播放实例）")
    if not leaders:
        raise SystemExit(
            "录音中未检测到 leader，无法自动切分。\n"
            "请确认：① 使用 v3 版测试音频（重新运行 gen 生成）；"
            "② 录音时长大于 leader 间隔（默认 30s）；"
            "③ 采集链未滤掉 chirp 频段（默认 10-14kHz）。")

    # 1.1) leader 起点精修：簇内每个 chirp 各自用伸缩匹配模板精修后反推簇首，
    # 交替扫频的残余偏差互相抵消，取中位数作为实例起点（粗检偏差为 (r-1) 比例、
    # 方向相关，实测此流程可把实例起点误差压到亚毫秒）
    gap_s = lcfg.gap_ms / 1000.0
    refined_leaders = []
    for ls, le in leaders:
        pk_in = [(t, d) for t, d in det if ls - 0.05 <= t <= le + 0.05]
        if len(pk_in) >= 2:
            r_est = float(np.clip((pk_in[-1][0] - pk_in[0][0])
                                  / ((len(pk_in) - 1) * gap_s), 0.9, 1.1))
        else:
            r_est = 1.0
        ests = [_refine_marker(rec_bp, sr, cfg, d == "up", t, r_est, win_ms=25.0)
                - j * gap_s * r_est for j, (t, d) in enumerate(pk_in)]
        ls2 = float(np.median(ests)) if ests else ls
        refined_leaders.append((ls2, le))
    leaders = refined_leaders

    # 2) 逐实例：识别 → 对齐 → 评分
    cands = load_ref_db(ref_dir)
    ref_cache: dict[str, np.ndarray] = {}
    # 循环判定：同一 song_id 的本段起始标记序号比上一段小（歌曲重新开始）→ loop+1
    song_state: dict[str, dict] = {}
    instances: list[dict] = []
    all_rows: list[dict] = []
    for k, (ls, le) in enumerate(leaders):
        end = leaders[k + 1][0] if k + 1 < len(leaders) else total_s
        if end - ls < MIN_INSTANCE_S:
            print(f"实例 {k}: 时长 {end - ls:.1f}s 过短，跳过")
            continue
        a, b = int(ls * sr), int(end * sr)
        inst_m = rec_m[a:b]
        pk = [(t - ls, d) for t, d in body_peaks if ls - 0.05 <= t < end]
        print(f"实例 {k}: 录音 {ls:.1f}s ~ {end:.1f}s，正文标记峰 {len(pk)} 个")
        best = identify_instance(inst_m, sr, pk, cands, ref_cache)
        if best is None or best[0] < IDENTIFY_MIN_SCORE:
            print(f"  → 识别失败（最佳验证分 "
                  f"{best[0] if best else 0:.3f}），标记 unknown 并跳过")
            instances.append({"instance": k, "song_id": "unknown",
                              "rec_start_s": ls, "rec_end_s": end})
            continue
        vscore, cand = best
        song_id = cand["song_id"]

        inst_dir = out_dir / f"inst_{k:02d}_{_safe_name(song_id)}"
        inst_dir.mkdir(parents=True, exist_ok=True)
        inst_wav = inst_dir / "instance.wav"
        sf.write(str(inst_wav), rec[a:b], sr, subtype="PCM_16")
        al = align(cand["wav"], cand["markers_json"], inst_wav,
                   inst_dir / "aligned")
        # 循环判定：本段起始标记序号比该歌曲上一段小 → 新一轮播放
        first_idx = al["matches"][0]["index"] if al["matches"] else 0
        st = song_state.setdefault(song_id, {"loop": 0, "last_first": None})
        if st["last_first"] is None or first_idx < st["last_first"]:
            st["loop"] += 1
        st["last_first"] = first_idx
        loop_no = st["loop"]
        print(f"  → 识别为「{song_id}」（第 {loop_no} 次循环，起始标记 #{first_idx}，"
              f"验证分 {vscore:.3f}）")

        rows = compute_rows(al, inst_dir / "aligned", peaq_bin=peaq_bin)
        for r in rows:
            r.update({"instance": k, "song_id": song_id, "loop": loop_no,
                      "rec_start_s": round(ls, 3), "rec_end_s": round(end, 3),
                      "rec_time_s": round(ls + r["ref_start_s"], 3)})
        all_rows += rows
        instances.append({"instance": k, "song_id": song_id, "loop": loop_no,
                          "first_marker": int(first_idx),
                          "rec_start_s": round(ls, 3), "rec_end_s": round(end, 3),
                          "identify_score": round(vscore, 4),
                          "align_dir": str(inst_dir / "aligned")})

    # 3) 汇总报告（按歌曲聚合 + 总体聚合）
    if not all_rows:
        raise SystemExit("没有识别成功的实例，无法出报告")
    rep = write_report(all_rows, out_dir, group_key="song_id",
                       title="musicq autoscore quality curve")
    (out_dir / "autoscore.json").write_text(
        json.dumps({"rec_wav": str(rec_wav), "instances": instances},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    return {"instances": instances, "rows": all_rows, **rep}
