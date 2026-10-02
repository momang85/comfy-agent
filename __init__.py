# -*- coding: utf-8 -*-
"""ComfyUI custom-node 入口：ComfyAgent Bridge。

本仓库既是 comfy-agent（大脑 Web UI）本体，也是一个可被 ComfyUI-Manager
直接安装的 custom node。这里只做一件事、并保持极轻：提供一个画布节点，
执行时确保 comfy-agent 的 Web UI（127.0.0.1:8899）在跑，并输出访问地址。

设计约束：
- 绝不在 ComfyUI 进程里 import brain/comfy_agent（隔离：Agent 崩溃不拖垮 ComfyUI）；
- Web UI 以 detached 子进程启动，ComfyUI 退出不影响它；
- 已在跑时绝不重复启动（探测 /api/status）。
"""
import json
import os
import subprocess
import sys
import urllib.request

NODE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_URL = "http://127.0.0.1:8899"
PY = sys.executable  # ComfyUI 自己的 python，纯标准库项目直接复用


def _web_alive() -> bool:
    try:
        with urllib.request.urlopen(f"{WEB_URL}/api/status", timeout=3) as r:
            return bool(json.loads(r.read().decode()).get("ok"))
    except Exception:
        return False


def _spawn_webui() -> tuple[bool, str]:
    if _web_alive():
        return True, "already running"
    log_path = os.path.join(NODE_DIR, ".comfy-agent", "logs")
    try:
        os.makedirs(log_path, exist_ok=True)
        # detached：脱离 ComfyUI 进程树；输出进 comfy-agent 自己的日志
        with open(os.path.join(log_path, "webui.log"), "ab") as log:
            subprocess.Popen(
                [PY, "-B", "-m", "brain", "--web"],
                cwd=NODE_DIR, stdout=log, stderr=log,
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
                start_new_session=True,
            )
    except Exception as e:  # 启动失败不影响画布执行
        return False, f"spawn failed: {e}"
    import time
    for _ in range(30):
        time.sleep(1.0)
        if _web_alive():
            return True, "started"
    return False, "started but not ready in 30s (see .comfy-agent/logs/webui.log)"


class ComfyAgentBridge:
    """确保 comfy-agent Web UI 在跑；输出访问地址。图里放一个跑一次即可。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "ensure_running": ("BOOLEAN", {"default": True}),
            },
            "optional": {
                "open_in_browser": ("BOOLEAN", {"default": False}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("url", "status")
    FUNCTION = "run"
    OUTPUT_NODE = True
    CATEGORY = "comfy-agent"

    def run(self, ensure_running=True, open_in_browser=False):
        if ensure_running:
            ok, how = _spawn_webui()
        else:
            ok, how = _web_alive(), "probe only"
        if open_in_browser:
            try:
                import webbrowser
                webbrowser.open(WEB_URL)
            except Exception:
                pass
        return {"ui": {"text": [f"comfy-agent Web UI: {WEB_URL} ({how})"]},
                "result": (WEB_URL, f"{'running' if ok else 'not running'} ({how})")}


NODE_CLASS_MAPPINGS = {"ComfyAgentBridge": ComfyAgentBridge}
NODE_DISPLAY_NAME_MAPPINGS = {"ComfyAgentBridge": "ComfyAgent Bridge (chat UI)"}
WEB_DIRECTORY = "./web"  # 无前端扩展，目录留空即可
