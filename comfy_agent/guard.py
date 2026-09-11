# -*- coding: utf-8 -*-
"""硬件安全护栏：GPU 温度熔断。

背景（实机实测）：MiniMax H3 视频渲染时 GPU 常态 82-84°C，续接任务冲到 87°C；
而运行期没有任何自动保护——只有 `scripts/run_complex_task.py`（测试脚本）里有熔断。
本模块把温度读取与阈值判断做成运行时能力，由服务侧监控循环调用。

阈值：环境变量 `GPU_TEMP_LIMIT`（默认 85°C）。设为 0 或负数可关闭熔断。
"""
from __future__ import annotations

import os
import shutil
import subprocess

GPU_TEMP_LIMIT = float(os.environ.get("GPU_TEMP_LIMIT", "85"))


def gpu_temp_c(timeout: float = 5.0):
    """读取 GPU 温度（摄氏度）。nvidia-smi 不可用/读取失败时返回 None。"""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=timeout)
        line = (out.stdout or "").strip().splitlines()
        return float(line[0]) if line else None
    except Exception:
        return None


def over_limit(temp_c, limit: float = None) -> bool:
    """是否超过熔断阈值（温度读取失败或阈值关闭时返回 False）。"""
    lim = GPU_TEMP_LIMIT if limit is None else limit
    if temp_c is None or lim <= 0:
        return False
    return float(temp_c) >= float(lim)
