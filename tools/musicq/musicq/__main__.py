"""支持 python -m musicq 调用。"""
import sys

from .cli import main

sys.exit(main())
