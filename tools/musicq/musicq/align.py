"""对齐：chirp 检测 → 网格匹配 → 伸缩偏差标定修正 → 分段消形变 → 精对齐。"""
from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal

from .chirp import ChirpConfig, detect_markers, make_chirp, strip_leader_clusters

GUARD_S = 0.150          # 段首尾保护带（避开 chirp 本体）
RATE_MIN, RATE_MAX = 0.9, 1.1   # 速率比 sanity clamp
FINE_SEARCH_MS = 10.0    # 精对齐整数样本搜索窗


def _to_mono(x: np.ndarray) -> np.ndarray:
    return x.mean(axis=1) if x.ndim == 2 else x


def load_for_align(path: Path, sr: int) -> np.ndarray:
    """读音频并重采样到 sr，保持声道数。"""
    data, in_sr = sf.read(str(path), always_2d=True)
    if in_sr != sr:
        g = np.gcd(in_sr, sr)
        data = signal.resample_poly(data, sr // g, in_sr // g, axis=0)
    return data.astype(np.float32)


def _parabolic(y: np.ndarray, k: int) -> float:
    """抛物线插值亚采样峰位偏移，k 为整数峰下标。"""
    if 0 < k < len(y) - 1:
        denom = y[k - 1] - 2 * y[k] + y[k + 1]
        if abs(denom) > 1e-12:
            return float(np.clip(0.5 * (y[k - 1] - y[k + 1]) / denom, -0.5, 0.5))
    return 0.0


# ---------------------------------------------------------------- 网格匹配

def _vote_anchors(ref_t, ref_dir, det_t, det_dir, interval, max_cand=32):
    """固定偏移投票找候选锚点，返回 [(score, marker序号, 峰序号)]。

    前 30s 窗口（漂移 < 1s，投票最干净）与全曲范围（录音可能只是歌曲中段
    片段，锚点落在前窗外）各投一次，合并去重。
    规则网格 + 漏检会造成整数倍间隔的几何歧义（多个候选同分），交由内容验证消歧。
    """
    def vote(early_peaks, early_marks):
        tol = 0.8
        cand = []
        for pi in early_peaks:
            for mi in early_marks:
                if ref_dir[mi] != det_dir[pi]:
                    continue
                off = det_t[pi] - ref_t[mi]
                score = 0
                for pj in early_peaks:
                    cc = [abs(det_t[pj] - (ref_t[mj] + off)) for mj in early_marks
                          if ref_dir[mj] == det_dir[pj]]
                    if cc and min(cc) < tol:
                        score += 1
                cand.append((score, mi, pi))
        return cand

    win = 30.0  # 前 30s 内漂移 < 1s，可用固定偏移投票
    cand = vote(np.where(det_t < win + 15)[0], np.where(np.array(ref_t) < win)[0])
    cand += vote(np.arange(len(det_t)), np.arange(len(ref_t)))
    # 按 (marker, peak) 去重，取高分
    best_by_pair: dict[tuple, int] = {}
    for score, mi, pi in cand:
        key = (int(mi), int(pi))
        best_by_pair[key] = max(best_by_pair.get(key, 0), score)
    cand = sorted(((s, mi, pi) for (mi, pi), s in best_by_pair.items()),
                  key=lambda c: -c[0])
    cand = [c for c in cand if c[0] >= 2]
    if not cand:
        raise RuntimeError("网格匹配失败：找不到一致的锚点（检测峰太少或音频不符）")
    best = cand[0][0]
    return [c for c in cand if c[0] >= best - 2][:max_cand]


def _walk_assign(ref_t, ref_dir, det_t, det_dir, mi0, pi0, interval):
    """从锚点向两个方向逐标记推进，用局部速率外推预测。返回 {marker序号: 峰序号}。"""
    assign: dict[int, int] = {mi0: pi0}

    def walk(step):
        rate = 1.0
        j0, p0 = mi0, pi0
        for i in range(mi0 + step, len(ref_t) if step > 0 else -1, step):
            span = abs(ref_t[i] - ref_t[j0])
            pred = det_t[p0] + (ref_t[i] - ref_t[j0]) * rate
            tol = min(0.35 * interval, 0.3 + 0.05 * span)
            best, best_err = None, tol
            for pj in range(len(det_t)):
                if pj in assign.values() or det_dir[pj] != ref_dir[i]:
                    continue
                err = abs(det_t[pj] - pred)
                if err <= best_err and (det_t[pj] - det_t[p0]) * step > -0.2:
                    best, best_err = pj, err
            if best is None:
                continue  # 漏检，跳过该标记
            r_new = (det_t[best] - det_t[p0]) / (ref_t[i] - ref_t[j0])
            if not (0.85 <= r_new <= 1.15):
                continue  # 隐含速率离谱 → 视为离群峰
            assign[i] = best
            rate = float(np.clip(0.7 * rate + 0.3 * r_new, RATE_MIN, RATE_MAX))
            j0, p0 = i, best

    walk(+1)
    walk(-1)
    return assign


def _validate_assignment(ref_m, deg_m, sr, ref_t, assign, det_t, n_probe=3):
    """内容验证：对匹配范围首/中/尾的段做撤销形变后的分块归一化相关。

    分块（0.4s）逐块精对齐可容忍段内残余漂移（伸缩边界落在段中部时，
    整段单一速率撤销无法对齐）；正确匹配 ~0.6-0.9，整数间隔错配 <0.1。
    """
    keys = sorted(assign)
    if len(keys) < 2:
        return 0.0
    picks = np.linspace(0, len(keys) - 2, min(n_probe, len(keys) - 1)).astype(int)
    chunk, search = int(0.4 * sr), int(0.015 * sr)
    g = int(GUARD_S * sr)
    vals = []
    for p in picks:
        i0, i1 = keys[p], keys[p + 1]
        ra, rb = ref_t[i0], ref_t[i1]
        da, db = det_t[assign[i0]], det_t[assign[i1]]
        ref_seg = ref_m[int(ra * sr):int(rb * sr)]
        deg_seg = deg_m[int(da * sr):int(db * sr)]
        if min(len(ref_seg), len(deg_seg)) < sr:
            continue
        r = float(np.clip(len(deg_seg) / len(ref_seg), RATE_MIN, RATE_MAX))
        f = Fraction(1.0 / r).limit_denominator(2000)
        deg_fix = signal.resample_poly(deg_seg, f.numerator, f.denominator)
        n = min(len(ref_seg), len(deg_fix))
        for s0 in range(g, n - g - chunk, chunk):
            a = ref_seg[s0:s0 + chunk]
            b0, b1 = max(s0 - search, 0), min(s0 + chunk + search, len(deg_fix))
            bwin = deg_fix[b0:b1]
            if len(bwin) <= chunk:
                continue
            corr = signal.fftconvolve(bwin, a[::-1], mode="valid")
            k = int(np.argmax(corr))
            vals.append(float(corr[k] / (np.linalg.norm(a)
                                         * np.linalg.norm(bwin[k:k + chunk]) + 1e-12)))
    return float(np.mean(vals)) if vals else 0.0


def match_grid(ref_markers: list[dict], det_peaks: list[tuple[float, str]],
               cfg: ChirpConfig, ref_m=None, deg_m=None, sr=None,
               ) -> tuple[dict[int, float], float]:
    """把检测峰粗对应到参考标记序号，容忍漏检与离群点。

    返回 ({标记序号: 检测时刻}, 内容验证得分)。提供 ref_m/deg_m/sr 时，
    对得分相近的候选锚点做内容相关性验证消歧；否则验证得分为匹配数。
    """
    ref_t = [m["time_s"] for m in ref_markers]
    ref_dir = [m["direction"] for m in ref_markers]
    det_t = np.array([t for t, _ in det_peaks])
    det_dir = [d for _, d in det_peaks]
    if len(det_t) < 3:
        raise RuntimeError(f"检测到 {len(det_t)} 个标记，不足以对齐")

    candidates = _vote_anchors(ref_t, ref_dir, det_t, det_dir, cfg.interval_s)
    # 先 walk 再按结果去重：同一平移假设只需验证一次（候选数可能远超 32 上限，
    # 直接截断候选会把正确平移挤掉 —— 真机中段片段实测踩中）
    unique: dict[tuple, tuple[int, dict]] = {}  # assign_key -> (vote, assign)
    for score, mi0, pi0 in candidates:
        assign = _walk_assign(ref_t, ref_dir, det_t, det_dir, mi0, pi0,
                              cfg.interval_s)
        if len(assign) < 3:
            continue
        key = tuple(sorted(assign.items()))
        if key not in unique or score > unique[key][0]:
            unique[key] = (score, assign)
    # 按投票分排序，最多验证 16 个互异假设
    hypos = sorted(unique.values(), key=lambda x: -x[0])[:16]
    best_assign, best_score = None, -1.0
    for vote, assign in hypos:
        if ref_m is not None:
            v = _validate_assignment(ref_m, deg_m, sr, ref_t, assign, det_t)
        else:
            v = float(len(assign))  # 无信号时退化为覆盖数最多
        if v > best_score:
            best_assign, best_score = assign, v
    if best_assign is None:
        raise RuntimeError("网格匹配失败：所有候选锚点覆盖的标记都太少")
    return ({int(i): float(det_t[best_assign[i]]) for i in sorted(best_assign)},
            float(best_score))


# ---------------------------------------------------- 伸缩匹配模板精修

def _refine_marker(xb: np.ndarray, sr: int, cfg: ChirpConfig, up: bool,
                   t_pred: float, r: float, win_ms: float = 40.0) -> float:
    """在 t_pred 附近做"速率网格 × 位置"联合精修。

    裸模板在时间伸缩下有与扫频方向相关的系统偏差（r=1.03 时上扫 +7ms / 下扫 -4ms），
    且伸缩边界附近段速率不代表 chirp 所在局部的真实速率，因此以 r 为中心做
    ±0.008 粗网格 + ±0.002 细网格的模板拉伸搜索，取相关峰最高者；
    峰位做抛物线亚采样插值。残余偏差实测 <0.5ms。
    """
    chirp = make_chirp(sr, cfg, up=up)
    w = int(win_ms / 1000 * sr)
    c = int(round(t_pred * sr))

    def try_rate(rr):
        f = Fraction(float(np.clip(rr, RATE_MIN, RATE_MAX))).limit_denominator(2000)
        tmpl = signal.resample_poly(chirp, f.numerator, f.denominator).astype(np.float64)
        n = len(tmpl)
        a, b = max(c - w, 0), min(c + n + w, len(xb))
        win = xb[a:b]
        if len(win) <= n + 4:
            return None
        corr = signal.fftconvolve(win, tmpl[::-1], mode="valid")
        energy = signal.fftconvolve(win * win, np.ones(n), mode="valid")
        ncorr = corr / (np.sqrt(np.maximum(energy, 0.0)
                                  * float(np.sum(tmpl ** 2))) + 1e-12)
        k = int(np.argmax(ncorr))
        return (a + k + _parabolic(ncorr, k)) / sr, float(ncorr[k])

    cands = [r + d for d in (-0.008, -0.004, 0.0, 0.004, 0.008)]
    best = None
    for rr in cands:
        res = try_rate(rr)
        if res and (best is None or res[1] > best[1]):
            best = (res[0], res[1], rr)
    if best is None or best[1] < 0.2:
        return t_pred  # 相关质量太差，维持原估计
    # 细定位：best±0.002 三点对 q(r) 做抛物线拟合得最优速率 r*，再用 r* 精修一次
    # （直接按 max-q 选网格点会引入 ±半格 × 偏差斜率(≈200ms/unit) 的选格噪声）
    lo = try_rate(best[2] - 0.002)
    hi = try_rate(best[2] + 0.002)
    if lo and hi:
        y0, y1, y2 = lo[1], best[1], hi[1]
        denom = y0 - 2 * y1 + y2
        if abs(denom) > 1e-12:
            dr = float(np.clip(0.002 * 0.5 * (y0 - y2) / denom, -0.002, 0.002))
            res = try_rate(best[2] + dr)
            if res and res[1] > best[1] - 0.02:  # 允许微小 q 波动，换更准的速率
                return res[0]
    return best[0]


def calibrate_positions(ref_markers: list[dict], coarse: dict[int, float],
                        deg_mono: np.ndarray, sr: int, cfg: ChirpConfig,
                        rounds: int = 2) -> dict[int, float]:
    """迭代精修标记位置：由当前位置估计各段速率 → 用伸缩匹配模板重定位 → 重估速率。

    最后补救单点漏检并剔除离群误配。
    """
    from .chirp import _bandpass
    ref_t = [m["time_s"] for m in ref_markers]
    ref_dir = [m["direction"] for m in ref_markers]
    xb = _bandpass(deg_mono, sr, cfg)
    pos = dict(coarse)
    for _ in range(rounds):
        keys = sorted(pos)
        seg_rate = {}
        for i0, i1 in zip(keys, keys[1:]):
            seg_rate[(i0, i1)] = float(np.clip(
                (pos[i1] - pos[i0]) / (ref_t[i1] - ref_t[i0]), RATE_MIN, RATE_MAX))
        new_pos = {}
        for i in keys:
            # chirp 落在该标记"之后"的区间内，优先取后向段速率
            r_loc = seg_rate.get((i, i + 1), seg_rate.get((i - 1, i), 1.0))
            new_pos[i] = _refine_marker(xb, sr, cfg, ref_dir[i] == "up",
                                        pos[i], r_loc)
        pos = new_pos

    # 单点漏检补救：仅当左右相邻段速率一致（说明空洞不在伸缩边界上）时内插
    keys = sorted(pos)
    for i0, i1 in zip(keys, keys[1:]):
        if i1 - i0 != 2:
            continue
        i = i0 + 1
        if (i0 - 1) not in pos or (i1 + 1) not in pos:
            continue
        r_left = (pos[i0] - pos[i0 - 1]) / (ref_t[i0] - ref_t[i0 - 1])
        r_right = (pos[i1 + 1] - pos[i1]) / (ref_t[i1 + 1] - ref_t[i1])
        r_hole = (pos[i1] - pos[i0]) / (ref_t[i1] - ref_t[i0])
        if abs(r_left - r_right) > 0.02 or not (min(r_left, r_right) - 0.01
                                                <= r_hole <= max(r_left, r_right) + 0.01):
            continue  # 空洞处可能跨伸缩边界，内插会引入大误差，宁可缺检
        pos[i] = pos[i0] + (pos[i1] - pos[i0]) * (ref_t[i] - ref_t[i0]) / (ref_t[i1] - ref_t[i0])

    # 剔除与前后邻居隐含速率都越界的误配点
    keys = sorted(pos)
    drop = set()
    for k in range(1, len(keys) - 1):
        i0, i1, i2 = keys[k - 1], keys[k], keys[k + 1]
        r1 = (pos[i1] - pos[i0]) / (ref_t[i1] - ref_t[i0])
        r2 = (pos[i2] - pos[i1]) / (ref_t[i2] - ref_t[i1])
        if not (RATE_MIN <= r1 <= RATE_MAX) and not (RATE_MIN <= r2 <= RATE_MAX):
            drop.add(i1)
    return {i: pos[i] for i in keys if i not in drop}


# ---------------------------------------------------------------- 段内精对齐

def _fine_shift(ref: np.ndarray, deg: np.ndarray, sr: int) -> float:
    """互相关估计 deg 相对 ref 的延迟（样本，含抛物线插值亚采样）。"""
    n = min(len(ref), len(deg))
    ref, deg = ref[:n], deg[:n]
    max_lag = int(FINE_SEARCH_MS / 1000 * sr)
    corr = signal.fftconvolve(ref, deg[::-1], mode="full")
    lags = np.arange(-(len(deg) - 1), len(ref))
    m = (lags >= -max_lag) & (lags <= max_lag)
    wl, wc = lags[m], corr[m]
    i = int(np.argmax(wc))
    return int(wl[i]) + _parabolic(wc, i)  # 正值：deg 比 ref 晚


def _apply_delay(x: np.ndarray, d: float) -> np.ndarray:
    """把 x 平移 d 个样本（y[n]=x[n-d]，d 可为分数，FFT 相移法）。"""
    k = int(np.floor(d))
    frac = d - k
    y = np.roll(x, k, axis=0)
    if k > 0:
        y[:k] = 0
    elif k < 0:
        y[k:] = 0
    if abs(frac) > 1e-9:
        n = len(y)
        freqs = np.fft.rfftfreq(n)
        phase = np.exp(-2j * np.pi * freqs * frac)
        if y.ndim == 2:
            y = np.fft.irfft(np.fft.rfft(y, axis=0) * phase[:, None], n, axis=0)
        else:
            y = np.fft.irfft(np.fft.rfft(y) * phase, n)
    return y.astype(np.float32)


# ---------------------------------------------------------------- 主流程

def align(ref_wav: Path, markers_json: Path, deg_wav: Path, out_dir: Path,
          cfg: ChirpConfig | None = None) -> dict:
    meta = json.loads(Path(markers_json).read_text(encoding="utf-8"))
    cfg = cfg or ChirpConfig(**meta["chirp"])
    sr = meta["sr"]
    ref = load_for_align(ref_wav, sr)
    deg = load_for_align(deg_wav, sr)

    det = detect_markers(_to_mono(deg).astype(np.float64), sr, cfg)
    det = strip_leader_clusters(det, cfg)  # leader 簇不参与正文网格匹配
    print(f"检测到 {len(det)} 个正文标记（参考 {len(meta['markers'])} 个）")
    coarse, _ = match_grid(meta["markers"], det, cfg,
                           ref_m=_to_mono(ref).astype(np.float64),
                           deg_m=_to_mono(deg).astype(np.float64), sr=sr)
    print(f"网格匹配成功 {len(coarse)} 个标记")
    matches = calibrate_positions(meta["markers"], coarse,
                                  _to_mono(deg).astype(np.float64), sr, cfg)
    print(f"伸缩模板精修+漏检补救后 {len(matches)} 个标记")

    out_dir.mkdir(parents=True, exist_ok=True)
    guard = int(GUARD_S * sr)
    keys = sorted(matches)
    segments = []
    for seg_i, (i0, i1) in enumerate(zip(keys, keys[1:])):
        ref_a, ref_b = meta["markers"][i0]["time_s"], meta["markers"][i1]["time_s"]
        deg_a, deg_b = matches[i0], matches[i1]
        ref_seg = ref[int(round(ref_a * sr)):int(round(ref_b * sr))]
        deg_seg = deg[int(round(deg_a * sr)):int(round(deg_b * sr))]
        if min(len(ref_seg), len(deg_seg)) < 2 * guard + sr // 2:
            continue
        # 速率比 + 撤销形变
        r = float(np.clip(len(deg_seg) / len(ref_seg), RATE_MIN, RATE_MAX))
        frac = Fraction(1.0 / r).limit_denominator(2000)
        deg_fix = signal.resample_poly(deg_seg, frac.numerator, frac.denominator, axis=0)
        # 段内精对齐（单声道估计、全声道应用）
        d = _fine_shift(_to_mono(ref_seg), _to_mono(deg_fix), sr)
        deg_al = _apply_delay(deg_fix, d)
        # 保护带 + 等长裁剪
        n = min(len(ref_seg), len(deg_al))
        ref_out = ref_seg[guard:n - guard]
        deg_out = deg_al[guard:n - guard]
        ref_name, deg_name = f"ref_{seg_i:03d}.wav", f"deg_{seg_i:03d}.wav"
        sf.write(str(out_dir / ref_name), ref_out, sr, subtype="PCM_16")
        sf.write(str(out_dir / deg_name), deg_out, sr, subtype="PCM_16")
        segments.append({
            "seg": seg_i, "ref_marker_range": [int(i0), int(i1)],
            "ref_start_s": float(ref_a), "ref_end_s": float(ref_b),
            "deg_start_s": float(deg_a), "deg_end_s": float(deg_b),
            "rate_ratio": r, "shift_samples": float(d),
            "shift_ms": float(d / sr * 1000.0),
            "ref_wav": ref_name, "deg_wav": deg_name,
        })

    result = {
        "sr": sr, "ref_wav": str(ref_wav), "deg_wav": str(deg_wav),
        "chirp": cfg.to_dict(),
        "matches": [{"index": int(i),
                     "ref_time_s": float(meta["markers"][i]["time_s"]),
                     "deg_time_s": float(matches[i]),
                     "offset_s": float(matches[i] - meta["markers"][i]["time_s"]),
                     "direction": meta["markers"][i]["direction"]} for i in keys],
        "segments": segments,
    }
    (out_dir / "alignment.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"输出 {len(segments)} 个对齐段 → {out_dir}")
    return result
