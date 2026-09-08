"""报告：逐段评分汇总 → report.csv + summary.txt + 质量曲线 PNG。

compute_rows / write_report 为可复用核心：align 命令与 autoscore 共用。
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

import matplotlib
matplotlib.use("Agg")  # 无显示环境后端
import matplotlib.pyplot as plt

from . import metrics

# 逐段指标列（除定位/元信息列外）
METRIC_COLS = ("visqol", "peaq_odg", "snr_db", "segsnr_db", "thd_n_db")


def _pct(values, q):
    return float(np.percentile(values, q)) if len(values) else float("nan")


def compute_rows(al: dict, align_dir: Path, peaq_bin: Path | None = None) -> list[dict]:
    """对 alignment.json 里的逐段 wav 对计算全部指标。"""
    align_dir = Path(align_dir)
    rows = []
    for seg in al["segments"]:
        ref_p, deg_p = align_dir / seg["ref_wav"], align_dir / seg["deg_wav"]
        m = metrics.compute_all(ref_p, deg_p, peaq_bin=peaq_bin)
        rows.append({
            "seg": seg["seg"],
            "ref_start_s": seg["ref_start_s"],
            "ref_end_s": seg["ref_end_s"],
            "rate_ratio": seg["rate_ratio"],
            "shift_ms": seg["shift_ms"],
            **m,
        })
    return rows


def _agg_lines(name: str, rows: list[dict]) -> list[str]:
    """一组行的指标聚合文本（中位数/P10/P90/均值 + 伸缩统计）。"""
    lines = [f"[{name}] 段数 {len(rows)}"]
    for col in METRIC_COLS:
        vals = [r[col] for r in rows if r.get(col) is not None]
        if not vals:
            lines.append(f"  {col}: 不可用（已跳过）")
            continue
        lines.append(f"  {col}: 中位数 {_pct(vals, 50):.3f}  P10 {_pct(vals, 10):.3f}"
                     f"  P90 {_pct(vals, 90):.3f}  均值 {np.mean(vals):.3f}")
    rates = [r["rate_ratio"] for r in rows]
    durs = [r["ref_end_s"] - r["ref_start_s"] for r in rows]
    lines += [f"  伸缩: 最大速率比 {max(rates):.4f}  最小 {min(rates):.4f}"
              f"  累计形变量 {sum(abs(r - 1.0) * d for r, d in zip(rates, durs)):.2f} s"]
    return lines


def write_report(rows: list[dict], out_dir: Path, group_key: str | None = None,
                 title: str = "musicq quality curve") -> dict:
    """写 report.csv + summary.txt + quality.png。

    group_key 给定时（如 song_id）按该字段分组聚合后再给总体聚合。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise RuntimeError("没有任何评分段，无法生成报告")

    # ---------- report.csv ----------
    csv_path = out_dir / "report.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # ---------- summary.txt ----------
    lines = ["musicq 音质评价汇总", "=" * 40, ""]
    if group_key:
        groups: dict[str, list[dict]] = {}
        for r in rows:
            groups.setdefault(str(r.get(group_key, "?")), []).append(r)
        for gname, grows in groups.items():
            lines += _agg_lines(gname, grows) + [""]
        lines += ["-" * 40, ""]
    lines += _agg_lines("总体", rows)
    summary = "\n".join(lines)
    (out_dir / "summary.txt").write_text(summary, encoding="utf-8")
    print(summary)

    # ---------- 质量曲线 PNG ----------
    x = [r.get("rec_time_s", r["ref_start_s"]) for r in rows]
    vis = [r.get("visqol") for r in rows]
    fig, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
    if any(v is not None for v in vis):
        axes[0].plot(x, [v if v is not None else np.nan for v in vis], "o-",
                     label="ViSQOL")
    axes[0].set_ylabel("ViSQOL (MOS-LQO)")
    axes[0].set_ylim(1, 5)
    axes[0].grid(True, alpha=0.3)
    axes[0].legend(loc="lower left")
    axes[1].plot(x, [r.get("snr_db") for r in rows], "s-", color="darkorange",
                 label="SNR dB")
    ax2 = axes[1].twinx()
    ax2.plot(x, [r["rate_ratio"] for r in rows], ".-", color="gray", alpha=0.6,
             label="rate ratio")
    ax2.set_ylabel("rate ratio")
    axes[1].set_ylabel("SNR (dB)")
    axes[1].set_xlabel("time (s)")
    axes[1].grid(True, alpha=0.3)
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(out_dir / "quality.png", dpi=120)
    plt.close(fig)

    return {"csv": str(csv_path), "summary": str(out_dir / "summary.txt"),
            "png": str(out_dir / "quality.png")}


def score_dir(align_dir: Path, peaq_bin: Path | None = None) -> dict:
    """mq score 入口：对 align 输出目录逐段评分并出报告。"""
    align_dir = Path(align_dir)
    al = json.loads((align_dir / "alignment.json").read_text(encoding="utf-8"))
    rows = compute_rows(al, align_dir, peaq_bin=peaq_bin)
    out = write_report(rows, align_dir)
    return {"rows": rows, **out}
