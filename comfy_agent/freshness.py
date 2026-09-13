# -*- coding: utf-8 -*-
"""引擎代码新鲜度：防止"改了代码但服务还在跑旧代码"。

项目 6 的事故根因就是这个：Web UI 进程 09:58 启动，我 10:08 之后才把
`local_repair` 加进 `INPUT_FILE_PARAMS`，而服务仍用旧代码运行 —— 引擎认为
该模板没有输入文件要上传，于是图片路径原样进了工作流，ComfyUI 的 LoadImage
以 `Invalid image file` 拒收，连着两次 `repair_failed`；更糟的是这些"技术失败"
还把止损额度用掉了，导致后续同一手法被反复拒绝。

这类"改了没生效"必须**显式可见**，而不是静默用旧代码。做法：启动时对关键
模块取指纹（mtime+size），每回合开始比对；不一致就告警、拒绝执行重活，
并在 `/api/status` 暴露 `stale_code` 供前端提示"请重启服务"。
"""
from __future__ import annotations

from pathlib import Path

#: 参与指纹的模块（跑在服务里的决策/执行路径；改任何一个都该重启）
WATCHED = (
    "comfy_agent/runner.py",
    "comfy_agent/validate.py",
    "comfy_agent/repair.py",
    "comfy_agent/knowledge.py",
    "comfy_agent/world.py",
    "comfy_agent/mask.py",
    "comfy_agent/model_download.py",
    "comfy_agent/templates/base.py",
    "comfy_agent/templates/image.py",
    "comfy_agent/templates/video.py",
    "comfy_agent/templates/__init__.py",
    "brain/agent.py",
    "brain/tools.py",
    "brain/task.py",
    "brain/policy.py",
    "brain/llm.py",
    "brain/web/server.py",
)


def _root() -> Path:
    return Path(__file__).resolve().parent.parent


def code_fingerprint(paths: tuple[str, ...] = WATCHED) -> dict:
    """关键模块的 {相对路径: "mtime_ns:size"} 指纹。"""
    out: dict[str, str] = {}
    root = _root()
    for rel in paths:
        p = root / rel
        try:
            st = p.stat()
            out[rel] = f"{st.st_mtime_ns}:{st.st_size}"
        except OSError:
            out[rel] = "missing"
    return out


def changed_since(snapshot: dict,
                  paths: tuple[str, ...] = WATCHED) -> list[str]:
    """返回自 snapshot 之后被改动（或新增/删除）的模块列表。"""
    now = code_fingerprint(paths)
    return sorted(k for k, v in now.items() if snapshot.get(k) != v)


def is_stale(snapshot: dict) -> bool:
    return bool(changed_since(snapshot))


def stale_message(changed: list[str]) -> str:
    files = "、".join(Path(c).name for c in changed[:4])
    more = f" 等 {len(changed)} 个文件" if len(changed) > 4 else ""
    return (f"引擎代码已更新（{files}{more}），当前服务仍在跑旧代码："
            "本轮不会执行任何生成，以免用旧逻辑白烧 GPU。"
            "请重启 Web UI（一键启动.bat）后再继续。")
