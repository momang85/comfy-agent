# -*- coding: utf-8 -*-
"""python -m brain 入口（委托 chat.py）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brain.chat import main

if __name__ == "__main__":
    main()
