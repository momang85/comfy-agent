# -*- coding: utf-8 -*-
"""Web UI 运行报告采集器：跑不同类型任务，抓 SSE 事件流做前后端一致性校验。

网络安全策略（本采集器的既定目标就是监控本机 Web UI，因此目标地址被
显式锁定为回环地址上的本地服务，并在构造时程序化校验；不接受任何
外部输入作为请求目标，故不存在 SSRF 风险面）。
"""
from __future__ import annotations

import ipaddress
import json
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

_HOST, _PORT = "127.0.0.1", 8899
OUT = Path(__file__).parent.parent / ".comfy-agent" / "run_report_data.json"
# 产物根目录从 config 导入（跨设备可移植）
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from comfy_agent import config as _cfg
RESULTS_DIR = _cfg.RESULTS_DIR


def _base_url() -> str:
    """构造并校验本地 UI 基址：仅接受回环地址 + http（本机监控专用）。"""
    u = urllib.parse.urlparse(f"http://{_HOST}:{_PORT}")
    assert u.scheme == "http", "仅允许 http"
    ip = ipaddress.ip_address(u.hostname or "")
    assert ip.is_loopback, "仅允许回环地址（本机 Web UI）"
    return f"http://{u.hostname}:{u.port}"


BASE = _base_url()


def sse_get():
    """打开 SSE 监听连接（目标已校验）。"""
    return urllib.request.urlopen(BASE + "/events", timeout=3600)


def post_message(text: str) -> bool:
    body = json.dumps({"text": text}).encode()
    req = urllib.request.Request(BASE + "/api/message", data=body,
                                 method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read()).get("ok", False)
    except Exception:
        return False


def get_status() -> dict:
    try:
        with urllib.request.urlopen(BASE + "/api/status", timeout=5) as r:
            return json.loads(r.read())
    except Exception:
        return {}


TASKS = [
    {"id": "image_single", "label": "图像单步生成",
     "text": "画一只赛博朋克风格的猫，霓虹灯背景", "timeout": 300},
    {"id": "failure_feedback", "label": "失败回流修复",
     "text": "把 comfy-agent/.comfy-agent/broken_wf.json 这个工作流加载并修好执行，内容是一只戴巫师帽的猫",
     "timeout": 240},
    {"id": "compose_pipeline", "label": "管线拼接（生成→放大）",
     "text": "生成一只柴犬在草地上的图片，然后放大两倍", "timeout": 480},
    {"id": "free_synthesis", "label": "从零自由合成",
     "text": "自由合成：用 anything-v5 从零搭一个 512x512 文生图，画一只雪地里的狐狸",
     "timeout": 300},
    {"id": "video_5s", "label": "5秒视频生成+抽帧评估",
     "text": "生成一个5秒视频：海浪拍打礁石，夕阳西下", "timeout": 1200},
]


def sse_listener(events: list, stop: threading.Event):
    buf = ""
    try:
        with sse_get() as r:
            while not stop.is_set():
                chunk = r.read(1)
                if not chunk:
                    break
                buf += chunk.decode("utf-8", "replace")
                while "\n\n" in buf:
                    raw, buf = buf.split("\n\n", 1)
                    for line in raw.splitlines():
                        if line.startswith("data: "):
                            try:
                                e = json.loads(line[6:])
                                e["recv_ts"] = time.time()
                                events.append(e)
                            except json.JSONDecodeError:
                                pass
    except Exception as e:
        events.append({"event": "listener_error",
                       "data": {"msg": str(e)[:120]}, "recv_ts": time.time()})


def wait_idle(timeout: float = 120) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if get_status().get("stage") == "idle":
            return True
        time.sleep(2)
    return False


def check_task(task: dict, evs: list, t0: float) -> dict:
    kinds = [e["event"] for e in evs]
    checks = {}
    checks["order_ok"] = (bool(kinds) and kinds[0] == "user_message"
                          and "delivery" in kinds)
    wf_events = [e for e in evs if e["event"] == "workflow_update"]
    graph_checks = []
    for we in wf_events:
        g = we["data"].get("graph") or {}
        ids = set(g.keys())
        bad_edges = 0
        for node in g.values():
            for v in (node.get("inputs") or {}).values():
                if isinstance(v, list) and len(v) == 2 and \
                        isinstance(v[0], str) and v[0] not in ids:
                    bad_edges += 1
        graph_checks.append({"nodes": len(ids), "bad_edges": bad_edges,
                             "valid": bad_edges == 0 and len(ids) > 0})
    checks["workflow_updates"] = len(wf_events)
    checks["graph_checks"] = graph_checks
    checks["graph_all_valid"] = all(g["valid"] for g in graph_checks) \
        if graph_checks else None
    stages = [e["data"].get("stage") for e in evs if e["event"] == "stage"]
    checks["stages"] = stages
    checks["has_execution"] = any(s in ("running", "completed")
                                  for s in stages if s)
    evals = [e["data"] for e in evs if e["event"] == "evaluation"]
    checks["evaluations"] = [{"kind": e.get("kind"), "verdict": e.get("verdict"),
                              "score": e.get("score")} for e in evals]
    outs = []
    for e in evs:
        if e["event"] == "delivery":
            for p in e["data"].get("outputs", []):
                outs.append({"path": p, "exists": Path(p).exists()})
    checks["outputs"] = outs
    checks["outputs_all_exist"] = all(o["exists"] for o in outs) if outs else None

    def first_ts(kind):
        for e in evs:
            if e["event"] == kind:
                return e["recv_ts"] - t0
        return None
    checks["latency"] = {
        "first_think_s": round(first_ts("think_delta") or -1, 2),
        "first_tool_s": round(first_ts("tool_start") or -1, 2),
        "delivery_s": round(first_ts("delivery") or -1, 2),
    }
    checks["tool_calls"] = kinds.count("tool_start")
    checks["think_chars"] = sum(len(e["data"].get("delta", ""))
                                for e in evs if e["event"] == "think_delta")
    checks["tool_failures"] = kinds.count("tool_start") - \
        sum(1 for e in evs if e["event"] == "tool_end" and e["data"].get("ok"))
    return checks


def main():
    stop = threading.Event()
    events: list = []
    t = threading.Thread(target=sse_listener, args=(events, stop), daemon=True)
    t.start()
    time.sleep(1.5)

    report_tasks = []
    for task in TASKS:
        print(f"\n=== 任务 [{task['label']}] ===")
        if not wait_idle():
            print("  等待空闲超时，跳过")
            report_tasks.append({"id": task["id"], "label": task["label"],
                                 "error": "not_idle"})
            continue
        mark = len(events)
        t0 = time.time()
        if not post_message(task["text"]):
            report_tasks.append({"id": task["id"], "label": task["label"],
                                 "error": "post_failed"})
            continue
        deadline = time.time() + task["timeout"]
        got_delivery = False
        while time.time() < deadline:
            if any(e["event"] == "delivery" for e in events[mark:]):
                got_delivery = True
                break
            time.sleep(3)
        evs = events[mark:]
        checks = check_task(task, evs, t0)
        entry = {"id": task["id"], "label": task["label"],
                 "text": task["text"], "delivered": got_delivery,
                 "events": len(evs), "checks": checks,
                 "wall_s": round(time.time() - t0, 1)}
        report_tasks.append(entry)
        print(f"  交付: {got_delivery} | 事件: {len(evs)} | "
              f"耗时: {entry['wall_s']}s | 图校验: {checks['graph_all_valid']} | "
              f"产物存在: {checks['outputs_all_exist']}")
        time.sleep(3)

    stop.set()
    aggregate = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                 "tasks": report_tasks, "total_events": len(events)}
    OUT.write_text(json.dumps(aggregate, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"\n数据已保存: {OUT}")


if __name__ == "__main__":
    main()
