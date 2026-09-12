# -*- coding: utf-8 -*-
"""硬件安全护栏：GPU 温度熔断。

背景（实机实测）：MiniMax H3 视频渲染时 GPU 常态 82-84°C，续接任务冲到 87°C；
而运行期没有任何自动保护——只有 `scripts/run_complex_task.py`（测试脚本）里有熔断。
本模块把温度读取与阈值判断做成运行时能力，由服务侧监控循环调用。

阈值：`GPU_TEMP_WARN`（默认 88°C，只提醒）与 `GPU_TEMP_LIMIT`（默认 92°C，中断任务）。设为 0 或负数可关闭。
"""
from __future__ import annotations

import os
import shutil
import subprocess

GPU_TEMP_WARN = float(os.environ.get("GPU_TEMP_WARN", "88"))
GPU_TEMP_LIMIT = float(os.environ.get("GPU_TEMP_LIMIT", "92"))


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


def comfy_root_from_file() -> str:
    """从项目根的 comfy_root.local 读取 ComfyUI 路径（一键启动.bat 写的那份）。"""
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "comfy_root.local"
    try:
        if p.exists():
            lines = p.read_text(encoding="utf-8", errors="replace").strip().splitlines()
            return lines[0].strip() if lines else ""
    except OSError:
        pass
    return ""


def try_start_comfy(timeout: float = 120.0) -> bool:
    """ComfyUI 不可达时尽力拉起（仅当 AUTO_START_COMFY=1，默认关闭）。

    实测 ComfyUI 会被大模型拖崩，之后所有任务只能报"连不上"。开启本开关后
    引擎在提交前自愈；路径取 env COMFY_ROOT 或 comfy_root.local。"""
    import time
    from pathlib import Path

    if os.environ.get("AUTO_START_COMFY", "0") != "1":
        return False
    root = os.environ.get("COMFY_ROOT") or comfy_root_from_file()
    if not root:
        return False
    py = Path(root) / "python" / "python.exe"
    main = Path(root) / "ComfyUI" / "main.py"
    if not py.exists() or not main.exists():
        return False
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | \
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        subprocess.Popen(
            [str(py), "-B", str(main), "--reserve-vram", "1.5",
             "--force-channels-last"],
            cwd=str(root), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=flags)
    except OSError:
        return False
    from .client import Client
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if Client().is_alive(timeout=2):
                return True
        except Exception:
            pass
        time.sleep(3)
    return False
