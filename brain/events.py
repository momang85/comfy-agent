# -*- coding: utf-8 -*-
"""事件总线：大脑/引擎所有动作的类型化事件流（前端可视化的唯一真相源）。

适配器：
  - 控制台：现有 print 保留，事件是增量的（不破坏 CLI 体验）
  - SSE：web/server.py 挂订阅者推送浏览器
  - 会话：SessionAdapter 追加写 sessions/*.jsonl（审计/回放）

事件类型（前端契约）：
  user_message     {text, ts}
  memory_recalled  {skills: [...]}
  think_delta      {channel: "reasoning"|"content", delta: str}   # 流式思考
  think_done       {content, reasoning}
  tool_start       {tool, args}
  tool_end         {tool, ok, summary, detail}
  workflow_update  {graph: {node_id: {...}}, meta: {...}}
  stage            {stage, detail, ts}       # 五段管线
  progress         {prompt_id, node_id, percent, elapsed}
  evaluation       {kind, verdict, score, issues, files}
  delivery        {text, outputs}
  skill_remembered {task, result}
  vram            {total_gb, free_gb}
  error           {where, message}
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from comfy_agent import config


class EventBus:
    def __init__(self):
        self._subs: list = []
        self._lock = threading.Lock()

    def subscribe(self, fn):
        with self._lock:
            self._subs.append(fn)

    def unsubscribe(self, fn):
        with self._lock:
            if fn in self._subs:
                self._subs.remove(fn)

    def emit(self, event: str, data: dict = None):
        msg = {"event": event, "data": data or {}, "ts": time.time()}
        self.emit_raw(msg)
        return msg

    def emit_raw(self, msg: dict):
        """直接广播已组装的消息（emit() 的项目标签包装走这里）。"""
        with self._lock:
            subs = list(self._subs)
        for fn in subs:
            try:
                fn(msg)
            except Exception:
                pass    # 订阅者故障不影响主线
        return msg


# 全局单例
bus = EventBus()

# 线程本地项目上下文：emit 时自动为事件打项目标签（项目隔离的关键）
_local = threading.local()


def set_project_context(project_id):
    _local.project = project_id


def clear_project_context():
    _local.project = None


class project_context:
    """with project_context(pid): 作用域内所有 emit 自动带上 project 标签。"""

    def __init__(self, project_id):
        self.pid = project_id

    def __enter__(self):
        set_project_context(self.pid)

    def __exit__(self, *exc):
        clear_project_context()
        return False


def emit(event: str, data: dict = None):
    msg = {"event": event, "data": data or {}, "ts": time.time(),
           "project": getattr(_local, "project", None)}
    bus.emit_raw(msg)
    return msg


def subscribe(fn, project_filter=None):
    """订阅事件。project_filter 给定时只接收该项目的消息（None=全部）。"""
    if project_filter is None:
        bus.subscribe(fn)
        return
    bus.subscribe(lambda m: fn(m) if m.get("project") == project_filter else None)


# ---------------- 会话适配器（审计/回放） ----------------

class SessionAdapter:
    """把事件流追加写入 sessions/session_<ts>.jsonl。"""

    def __init__(self, path: Path = None, project_filter=None):
        if path is None:
            ts = time.strftime("%Y%m%d_%H%M%S")
            path = config.SESSIONS_DIR / f"session_{ts}.jsonl"
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._filter = project_filter
        subscribe(self.on_event, self._filter)

    def on_event(self, msg: dict):
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(msg, ensure_ascii=False,
                                   default=str) + "\n")


def attach_session_logger() -> SessionAdapter:
    a = SessionAdapter()
    bus.subscribe(a.on_event)
    return a


def replay(path: str | Path) -> list[dict]:
    """回放会话事件（调试/审计）。"""
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out
