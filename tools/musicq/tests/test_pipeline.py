"""全链路合成自测：chirp 对齐精度 + ViSQOL 分数单调性 + autoscore 自动识别（无需真实设备）。

运行方式（在 tools/musicq 目录下）：
    .venv/Scripts/python.exe -m pytest tests/ -v
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from musicq.chirp import ChirpConfig, LeaderConfig, make_chirp, detect_chirps
from musicq.generate import MARKERS_VERSION, build_test_audio, load_audio
from musicq.simulate import run_synthetic_check, synthesize_degradation

MUSIC_DIR = Path(r"D:\music")
DURATION_S = 60.0

pytestmark = pytest.mark.skipif(
    not MUSIC_DIR.is_dir() or not list(MUSIC_DIR.glob("*.mp3")),
    reason="需要 D:\\music 音源库")

SRC = sorted(MUSIC_DIR.glob("*.mp3"))[0]


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    """三个劣化档位各跑一次全链路（模块级缓存，避免重复计算）。"""
    base = tmp_path_factory.mktemp("musicq_test")
    out = {}
    for strength in ("copy", "mild", "strong"):
        out[strength] = run_synthetic_check(
            SRC, base / strength, seed=7, strength=strength, duration_s=DURATION_S)
    return out


# ---------------------------------------------------------------- 单元级

def test_chirp_detect_clean():
    """无噪环境下 chirp 检测位置精度应到样本级。"""
    sr = 48000
    cfg = ChirpConfig()
    x = np.zeros(sr * 3, dtype=np.float64)
    t0 = 1.234
    c = make_chirp(sr, cfg, up=True)
    x[int(t0 * sr):int(t0 * sr) + len(c)] += c
    times = detect_chirps(x, sr, cfg, up=True)
    assert len(times) == 1
    assert abs(times[0] - t0) < 0.5 / sr * 2


# ---------------------------------------------------------------- 链路级

def test_alignment_accuracy(results):
    """各段速率比估计误差 < 0.2%，固定延迟估计误差（逐标记误差的中位数）< 0.5ms。"""
    for strength in ("copy", "mild", "strong"):
        s = results[strength]["summary"]
        assert s["n_segments"] >= 5, f"{strength}: 对齐段数过少 {s}"
        assert s["max_rate_error"] < 0.002, \
            f"{strength}: 速率比误差 {s['max_rate_error']:.5f} 超差"
        assert abs(s["delay_est_error_s"]) < 0.0005, \
            f"{strength}: 固定延迟估计误差 {s['delay_est_error_s'] * 1000:.3f}ms 超差"


def test_visqol_copy_near_perfect(results):
    """原样拷贝经对齐后 ViSQOL 应接近满分（≥4.7）。"""
    v = results["copy"]["summary"]["visqol_median"]
    assert v is not None, "ViSQOL 不可用"
    assert v >= 4.7, f"copy 档 ViSQOL={v:.3f}"


def test_visqol_monotonic(results):
    """劣化越重 ViSQOL 越低，且劣化版显著低于原样拷贝。"""
    v_copy = results["copy"]["summary"]["visqol_median"]
    v_mild = results["mild"]["summary"]["visqol_median"]
    v_strong = results["strong"]["summary"]["visqol_median"]
    assert v_mild < v_copy - 0.1, f"mild({v_mild:.3f}) 未显著低于 copy({v_copy:.3f})"
    assert v_strong < v_mild, f"strong({v_strong:.3f}) 应低于 mild({v_mild:.3f})"


def test_snr_sanity(results):
    """SNR sanity：劣化越重 SNR 越低。"""
    s = {k: results[k]["summary"]["snr_median_db"] for k in ("copy", "mild", "strong")}
    assert s["copy"] > s["mild"] > s["strong"], f"SNR 不单调: {s}"


# ---------------------------------------------------------------- autoscore

SONG_BODY_S = 45.0          # autoscore 自测用片段长度，控制耗时
AS_DEGRADE = dict(rate_range=(0.99, 1.01), lpf_hz=17000.0, noise_dbfs=-40.0)


def _make_ref_song(src: Path, ref_dir: Path, name: str, sr: int = 48000):
    """用 generate 的同一条路径生成 v3 短版测试音频（歌头+周期性 leader），写进 ref_dir。"""
    cfg, lcfg = ChirpConfig(), LeaderConfig()
    audio, sr = load_audio(src)
    audio = audio[:int(SONG_BODY_S * sr)]
    test_audio, markers, leader = build_test_audio(audio, sr, cfg, lcfg)
    wav = ref_dir / f"{name}_test.wav"
    sf.write(str(wav), test_audio, sr, subtype="PCM_16")
    meta = {"version": MARKERS_VERSION, "song_id": name, "source": src.name,
            "sr": sr, "duration_s": round(len(test_audio) / sr, 6),
            "chirp": cfg.to_dict(), "leader": leader, "markers": markers}
    (ref_dir / f"{name}_markers.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


@pytest.fixture(scope="module")
def autoscore_result(tmp_path_factory):
    """合成长录音：歌曲A×1 + 歌曲B×2（45s 各含 2 个周期 leader，共 6 段）→ autoscore。

    每段独立随机延迟/伸缩/噪声。周期 leader 把每个 45s 实例再切成 2 个 ~30s 段。
    """
    from musicq.autoscore import autoscore

    base = tmp_path_factory.mktemp("musicq_autoscore")
    ref_dir = base / "ref"
    ref_dir.mkdir()
    srcs = sorted(MUSIC_DIR.glob("*.mp3"))
    meta_a = _make_ref_song(srcs[0], ref_dir, "songA")
    meta_b = _make_ref_song(srcs[1], ref_dir, "songB")

    sr = 48000
    rng = np.random.default_rng(11)
    rec_pieces = [np.zeros(int(0.3 * sr), np.float32)]  # 开头无前导 leader 的碎片
    occs = []          # 每次播放（歌曲实例）的真值信息
    inst_starts = [0.3]
    for k, meta in enumerate((meta_a, meta_b, meta_b)):  # A×1 + B×2
        audio, _ = sf.read(str(ref_dir / f"{meta['song_id']}_test.wav"),
                           always_2d=True)
        mt = [m["time_s"] for m in meta["markers"]]
        leader_ts = [l["start_s"] for l in meta["leader"]["leaders"]]
        truth = synthesize_degradation(audio.astype(np.float32), sr, mt,
                                       seed=100 + k, n_stretch=(2, 2),
                                       delay_s=float(rng.uniform(0.05, 0.4)),
                                       extra_times=leader_ts,
                                       **AS_DEGRADE)
        rec_pieces.append(truth["deg"].mean(axis=1))
        rec_pieces.append(np.zeros(int(1.2 * sr), np.float32))  # 实例间隔静音
        # 各周期 leader 在录音中的真值位置（切点真值）
        cut_trues = [inst_starts[-1] + truth["true_extra_times"][lt]
                     for lt in leader_ts]
        occs.append({"song_id": meta["song_id"], "meta": meta, "truth": truth,
                     "cut_trues": cut_trues})
        inst_starts.append(inst_starts[-1] + len(truth["deg"]) / sr + 1.2)
    rec = np.concatenate(rec_pieces)
    rec_wav = base / "rec.wav"
    sf.write(str(rec_wav), rec, sr, subtype="PCM_16")

    # 参照分：对实例1（songB 第一次）的两个 30s 子段直接走单曲 align+score
    # （与 autoscore 切出的子段同内容、同一套 align+score 代码路径）
    from musicq.align import align
    from musicq.report import compute_rows
    occ1 = occs[1]
    deg1 = rec_pieces[3]
    cut1 = occ1["cut_trues"][1] - inst_starts[1]  # 子段边界（实例内相对秒）
    ref_meds = []
    for sub, (sa, sb) in enumerate([(0.0, cut1), (cut1, len(deg1) / sr)]):
        sub_wav = base / f"ref_sub{sub}.wav"
        sf.write(str(sub_wav), deg1[int(sa * sr):int(sb * sr)], sr, subtype="PCM_16")
        al_ref = align(ref_dir / "songB_test.wav", ref_dir / "songB_markers.json",
                       sub_wav, base / f"ref_single{sub}")
        rows = compute_rows(al_ref, base / f"ref_single{sub}")
        ref_meds.append(float(np.median([r["visqol"] for r in rows
                                         if r.get("visqol") is not None])))

    out_dir = base / "report"
    res = autoscore(rec_wav, ref_dir, out_dir)
    return {"res": res, "occs": occs, "out_dir": out_dir, "ref_meds": ref_meds}


def test_autoscore_instances(autoscore_result):
    """周期 leader 切分：3 次播放 × 各 2 个 leader → 6 个实例，歌名/循环全对。"""
    insts = autoscore_result["res"]["instances"]
    ids = [i["song_id"] for i in insts]
    assert ids == ["songA", "songA", "songB", "songB", "songB", "songB"], \
        f"实例切分/识别错误: {ids}"
    assert [i["loop"] for i in insts] == [1, 1, 1, 1, 2, 2], \
        f"循环序号错误: {[i['loop'] for i in insts]}"


def test_autoscore_alignment_accuracy(autoscore_result):
    """每个 30s 实例的对齐精度与单曲流程同等水平。

    断言分解（autoscore 的实例时间轴由 leader 检测定义）：
    - 各段速率比误差 < 0.2%（与单曲流程相同）
    - leader 切点误差 < 3ms（仅影响报告时间轴）
    - 正文标记一致性：逐标记误差去常数后最大偏差 < 1.5ms
    """
    res, occs = autoscore_result["res"], autoscore_result["occs"]
    assert len(res["instances"]) == 2 * len(occs)
    for occ_i, occ in enumerate(occs):
        truth = occ["truth"]
        for sub in (0, 1):
            inst = res["instances"][occ_i * 2 + sub]
            cut_true = occ["cut_trues"][sub]
            al = json.loads((Path(inst["align_dir"]) / "alignment.json")
                            .read_text(encoding="utf-8"))
            # leader 切点误差
            cut_err = abs(inst["rec_start_s"] - cut_true)
            assert cut_err < 0.003, \
                f"实例{inst['instance']} leader 切点误差 {cut_err * 1000:.2f}ms"
            # 各段速率比误差（真值由标记真值位置直接算，跨边界也精确）
            for seg in al["segments"]:
                t0, t1 = seg["ref_start_s"], seg["ref_end_s"]
                r_true = ((truth["true_deg_times"][t1] - truth["true_deg_times"][t0])
                          / (t1 - t0))
                assert abs(seg["rate_ratio"] - r_true) < 0.002, \
                    f"实例{inst['instance']} 段{seg['seg']} 速率误差超差"
            # 正文标记一致性（去除实例常数偏移）
            # 切点在劣化实例内的相对真值 = 该周期 leader 起点的真值位置
            leader_ts = [l["start_s"] for l in occ["meta"]["leader"]["leaders"]]
            cut_rel = truth["true_extra_times"][leader_ts[sub]]
            errs = []
            for m in al["matches"]:
                t = m["ref_time_s"]
                if t in truth["true_deg_times"]:
                    errs.append(m["deg_time_s"] - (truth["true_deg_times"][t] - cut_rel))
            assert errs, f"实例{inst['instance']} 无匹配标记"
            med = float(np.median(errs))
            spread = max(abs(e - med) for e in errs)
            assert spread < 0.0015, \
                f"实例{inst['instance']} 标记一致性 {spread * 1000:.2f}ms 超差"


def test_autoscore_scores(autoscore_result):
    """autoscore 30s 子段分数应与同内容单曲流程参照分一致（<0.1），报告产物齐全。"""
    res = autoscore_result["res"]
    rows = res["rows"]
    ref_meds = autoscore_result["ref_meds"]
    for inst in res["instances"]:
        vis = [r["visqol"] for r in rows
               if r["instance"] == inst["instance"] and r.get("visqol") is not None]
        assert vis, f"实例{inst['instance']} 没有 ViSQOL 分数"
        assert np.median(vis) >= 2.5, \
            f"实例{inst['instance']} ViSQOL 中位数 {np.median(vis):.3f} 异常偏低"
    # 实例 2/3 = songB 第一次播放的两个子段，与参照分逐段对比
    for sub, inst_i in ((0, 2), (1, 3)):
        vis = [r["visqol"] for r in rows
               if r["instance"] == inst_i and r.get("visqol") is not None]
        med = float(np.median(vis))
        assert abs(med - ref_meds[sub]) < 0.1, \
            f"实例{inst_i} 中位数 {med:.3f} 与单曲参照 {ref_meds[sub]:.3f} 差距过大"
    out = autoscore_result["out_dir"]
    for f in ("report.csv", "summary.txt", "quality.png", "autoscore.json"):
        assert (out / f).exists(), f"缺少报告产物 {f}"
