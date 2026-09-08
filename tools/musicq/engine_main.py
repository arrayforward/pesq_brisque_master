"""PyInstaller 冻结入口：等价于 python -m musicq。

构建（在 tools/musicq 目录下）：
    .venv/Scripts/python.exe -m PyInstaller --noconfirm --clean --onedir \
        --name musicq-engine --paths . \
        --collect-data visqol --collect-all soundfile --collect-all libsvm \
        --exclude-module numba --exclude-module tkinter \
        --distpath dist_engine --workpath build_engine engine_main.py
"""
import sys

# 冻结后是 Windows 控制台程序，stdout 默认按系统代码页(GBK)输出，
# GUI 按 UTF-8 解码会乱码 —— 在任何输出前强制 UTF-8（双保险：GUI 侧还设 PYTHONIOENCODING）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from musicq.cli import main

if __name__ == "__main__":
    sys.exit(main())
