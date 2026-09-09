"""musicq 命令行入口：gen / capture / extract / align / score / simulate。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

from .chirp import ChirpConfig, LeaderConfig
from . import align as align_mod
from . import autoscore as autoscore_mod
from . import capture as capture_mod
from . import generate as generate_mod
from . import report as report_mod
from . import simulate as simulate_mod

DEFAULT_MUSIC_DIR = r"D:\music"


def _add_chirp_opts(p: argparse.ArgumentParser):
    p.add_argument("--f0", type=float, default=10000.0, help="chirp 起始频率 Hz")
    p.add_argument("--f1", type=float, default=14000.0, help="chirp 终止频率 Hz")
    p.add_argument("--dur-ms", type=float, default=60.0, help="chirp 时长 ms")
    p.add_argument("--level", type=float, default=-24.0, help="chirp 峰值电平 dBFS")
    p.add_argument("--interval", type=float, default=5.0, help="标记间隔 s")
    p.add_argument("--start", type=float, default=2.0, help="首个标记时刻 s")


def _cfg_from(args) -> ChirpConfig:
    return ChirpConfig(f0=args.f0, f1=args.f1, dur_ms=args.dur_ms,
                       level_dbfs=args.level, interval_s=args.interval,
                       start_s=args.start)


def _add_leader_opts(p: argparse.ArgumentParser):
    p.add_argument("--leader-n", type=int, default=6, help="leader 簇内 chirp 数")
    p.add_argument("--leader-gap-ms", type=float, default=150.0,
                   help="leader 簇内 chirp 间隔 ms")
    p.add_argument("--leader-gap-after", type=float, default=0.5,
                   help="leader 结束到正文的间隙 s")
    p.add_argument("--leader-interval", type=float, default=30.0,
                   help="正文周期性 leader 间隔 s（<=0 仅歌头）")


def _lcfg_from(args) -> LeaderConfig:
    return LeaderConfig(n_chirps=args.leader_n, gap_ms=args.leader_gap_ms,
                        gap_after_s=args.leader_gap_after,
                        interval_s=args.leader_interval)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="musicq",
        description="音乐直播播放器端到端音质评价管线（chirp 对齐 + ViSQOL/PEAQ）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("gen", help="批量生成带 chirp 导频的测试音频")
    p.add_argument("input", nargs="?", default=DEFAULT_MUSIC_DIR,
                   help="音源 mp3 文件或目录（默认 %(default)s）")
    p.add_argument("-o", "--out", default=None, help="输出目录（默认 <输入目录>/musicq_out）")
    _add_chirp_opts(p)
    _add_leader_opts(p)

    p = sub.add_parser("capture", help="scrcpy 采集设备播放输出（手动起播，Ctrl+C 停止）")
    p.add_argument("-o", "--out", default="cap.mkv", help="输出录制文件")
    p.add_argument("--device", default=None, help="adb 设备序列号（多设备时指定）")

    p = sub.add_parser("extract", help="从录制文件抽取 48kHz 单声道 wav")
    p.add_argument("input", help="capture 得到的 mkv")
    p.add_argument("-o", "--out", default=None, help="输出 wav（默认同名 .wav）")

    p = sub.add_parser("btrecord", help="蓝牙 A2DP 采集：PC 模拟蓝牙音响 + 回环录制")
    p.add_argument("-o", "--out", required=True, help="输出 wav")
    p.add_argument("--seconds", type=float, default=300.0, help="录制时长秒（默认 300）")
    p.add_argument("--device", default=None, help="回环输出设备名（模糊匹配，默认系统默认输出）")
    p.add_argument("--no-sink", action="store_true",
                   help="不打开 A2DP sink（纯回环录制/已用其他方式开启 sink）")

    p = sub.add_parser("align", help="chirp 检测 + 网格匹配 + 分段消形变对齐")
    p.add_argument("--ref", required=True, help="参考测试 wav（gen 产物）")
    p.add_argument("--markers", required=True, help="标记时刻 json（gen 产物）")
    p.add_argument("--deg", required=True, help="采集 wav（extract 产物）")
    p.add_argument("-o", "--out", required=True, help="对齐输出目录")

    p = sub.add_parser("score", help="逐段评分 + 聚合报告")
    p.add_argument("--dir", required=True, help="align 输出目录（含 alignment.json）")
    p.add_argument("--peaq", default=None, help="外部 PEAQ 二进制路径（缺省跳过）")

    p = sub.add_parser("simulate", help="合成劣化全链路自测（无需真实设备）")
    p.add_argument("--src", default=None, help="音源（默认取 %s 第一首 mp3）" % DEFAULT_MUSIC_DIR)
    p.add_argument("-o", "--out", default="simulate_out", help="工作目录")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--strong", action="store_true", help="使用强劣化参数")

    p = sub.add_parser("autoscore",
                       help="长录音自动识别歌曲/循环 + 自动对齐打分出报告")
    p.add_argument("input", help="长时间采集录音 wav")
    p.add_argument("--ref-dir", required=True,
                   help="含 *_test.wav + *_markers.json 的参考目录")
    p.add_argument("-o", "--out", required=True, help="报告输出目录")
    p.add_argument("--peaq", default=None, help="外部 PEAQ 二进制路径（缺省跳过）")
    _add_chirp_opts(p)
    _add_leader_opts(p)

    args = ap.parse_args(argv)

    if args.cmd == "gen":
        src = Path(args.input)
        out = Path(args.out) if args.out else (
            src / "musicq_out" if src.is_dir() else src.parent / "musicq_out")
        generate_mod.generate(src, out, _cfg_from(args), _lcfg_from(args))

    elif args.cmd == "capture":
        capture_mod.capture(Path(args.out), device=args.device)

    elif args.cmd == "extract":
        inp = Path(args.input)
        out = Path(args.out) if args.out else inp.with_suffix(".wav")
        capture_mod.extract(inp, out)

    elif args.cmd == "btrecord":
        from . import btrecord as btrecord_mod
        btrecord_mod.btrecord(Path(args.out), seconds=args.seconds,
                              device=args.device, use_sink=not args.no_sink)

    elif args.cmd == "align":
        align_mod.align(Path(args.ref), Path(args.markers), Path(args.deg), Path(args.out))

    elif args.cmd == "score":
        report_mod.score_dir(Path(args.dir), peaq_bin=Path(args.peaq) if args.peaq else None)

    elif args.cmd == "simulate":
        src = Path(args.src) if args.src else _first_mp3(DEFAULT_MUSIC_DIR)
        res = simulate_mod.run_synthetic_check(
            src, Path(args.out), seed=args.seed,
            strength="strong" if args.strong else "standard")
        print(json.dumps(res["summary"], ensure_ascii=False, indent=2))

    elif args.cmd == "autoscore":
        autoscore_mod.autoscore(Path(args.input), Path(args.ref_dir), Path(args.out),
                                cfg=_cfg_from(args), lcfg=_lcfg_from(args),
                                peaq_bin=Path(args.peaq) if args.peaq else None)

    return 0


def _first_mp3(d) -> Path:
    files = sorted(Path(d).glob("*.mp3"))
    if not files:
        raise SystemExit(f"{d} 下没有 mp3")
    return files[0]


if __name__ == "__main__":
    sys.exit(main())
