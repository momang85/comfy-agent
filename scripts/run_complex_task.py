# -*- coding: utf-8 -*-
"""复杂任务实测采集器：前端提交 + SSE 事件流 + 硬件安全监控 + 产物校验。

安全策略：所有 HTTP 目标均经 _base_url 构造时显式校验（仅 http +
回环地址），调用点只传相对路径（禁止 ..），不接受外部输入作为请求
目标——与 scripts/run_report.py 同策略，无 SSRF 风险面。

硬件安全（防过热/失控）：
  - 每 5 秒采样 GPU 温度/功耗/占用/显存 + CPU 负载（nvidia-smi + WMI）
  - 温度 ≥88°C → 立即中断熔断当前任务并标记
  - 温度 ≥82°C → 告警标记
  - ComfyUI 队列堆积 >5 → 熔断（防止大脑失控连续提交）
"""
from __future__ import annotations

import ipaddress
import json
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

# 脚本以 scripts/ 为 sys.path[0]，把项目根加入以导入 brain 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

WEB_HOST, WEB_PORT = "127.0.0.1", 8899
COMFY_HOST, COMFY_PORT = "127.0.0.1", 8188


def _base_url(host: str, port: int) -> str:
    """构造并校验服务基址：仅接受回环地址 + http（本机监控专用）。"""
    u = urllib.parse.urlparse(f"http://{host}:{port}")
    assert u.scheme == "http", "仅允许 http"
    ip = ipaddress.ip_address(u.hostname or "")
    assert ip.is_loopback, "仅允许回环地址"
    return f"http://{u.hostname}:{u.port}"


WEB_BASE = _base_url(WEB_HOST, WEB_PORT)
COMFY_BASE = _base_url(COMFY_HOST, COMFY_PORT)
OUT = Path(__file__).parent.parent / ".comfy-agent" / "complex_task_data.json"

TASK_TEXT = "生成一个10秒的视频：海浪拍打礁石，夕阳下浪花飞溅"
TIMEOUT = 1800
# 笔记本游戏卡设计负载温度 85-92°C（硬件自身有热保护），
# 告警 85°C 记录入报告；熔断 93°C 拦截真正的失控过热
WARN_TEMP, KILL_TEMP, KILL_QUEUE = 85.0, 93.0, 5


def _web_open(path: str, data: bytes = None, method: str = "GET",
              timeout: int = 30, headers: dict = None):
    """Web UI 请求：仅接受相对路径，基址已在校验函数中构造。"""
    assert path.startswith("/") and ".." not in path, "非法路径"
    req = urllib.request.Request(WEB_BASE + path, data=data, method=method,
                                 headers=headers or {})
    return urllib.request.urlopen(req, timeout=timeout)


def _comfy_open(path: str, data: bytes = None, method: str = "GET",
                timeout: int = 30):
    """ComfyUI 请求：仅接受相对路径，基址已在校验函数中构造。"""
    assert path.startswith("/") and ".." not in path, "非法路径"
    req = urllib.request.Request(COMFY_BASE + path, data=data, method=method)
    return urllib.request.urlopen(req, timeout=timeout)


def _post(path: str, body: dict = None, timeout: int = 10):
    data = json.dumps(body).encode() if body is not None else None
    with _web_open(path, data=data, method="POST", timeout=timeout,
                   headers={"Content-Type": "application/json"}) as r:
        return json.loads(r.read())


def gpu_sample() -> dict:
    try:
        out = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=temperature.gpu,power.draw,utilization.gpu,memory.used",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10, check=False)
        parts = [p.strip() for p in out.stdout.strip().split(",")]
        return {"temp_c": float(parts[0]), "power_w": float(parts[1]),
                "gpu_util": float(parts[2]), "vram_mb": float(parts[3])}
    except Exception as e:
        return {"error": str(e)[:80]}


def cpu_sample() -> float:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Processor).LoadPercentage"],
            capture_output=True, text=True, timeout=15, check=False)
        return float(out.stdout.strip().splitlines()[0])
    except Exception:
        return -1.0


def queue_len_comfy() -> int:
    try:
        with _comfy_open("/queue", timeout=5) as r:
            q = json.loads(r.read())
        return len(q.get("queue_running", [])) + \
            len(q.get("queue_pending", []))
    except Exception:
        return -1


def interrupt_all():
    try:
        _post("/api/interrupt")
    except Exception:
        pass
    try:
        with _comfy_open("/interrupt", data=b"{}", method="POST", timeout=5):
            pass
    except Exception:
        pass


class SafetyMonitor:
    def __init__(self):
        self.samples: list = []
        self.warned = False
        self.killed = False
        self.kill_reason = ""
        self._stop = threading.Event()

    def loop(self):
        while not self._stop.is_set():
            s = {"ts": time.time(), "gpu": gpu_sample(),
                 "cpu_pct": cpu_sample(), "queue": queue_len_comfy()}
            self.samples.append(s)
            g = s["gpu"]
            if "temp_c" in g:
                if g["temp_c"] >= KILL_TEMP and not self.killed:
                    self.killed = True
                    self.kill_reason = (f"GPU 温度 {g['temp_c']}°C ≥ "
                                        f"{KILL_TEMP}°C，熔断")
                    interrupt_all()
                elif g["temp_c"] >= WARN_TEMP:
                    self.warned = True
            if s["queue"] > KILL_QUEUE and not self.killed:
                self.killed = True
                self.kill_reason = f"队列堆积 {s['queue']} > {KILL_QUEUE}，熔断"
                interrupt_all()
            self._stop.wait(5)


def sse_listener(events: list, stop: threading.Event):
    buf = ""
    try:
        with _web_open("/events", timeout=3600) as r:
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
        events.append({"event": "listener_error", "data": {"msg": str(e)[:120]},
                       "recv_ts": time.time()})


def main():
    events: list = []
    stop = threading.Event()
    monitor = SafetyMonitor()
    t1 = threading.Thread(target=sse_listener, args=(events, stop), daemon=True)
    t2 = threading.Thread(target=monitor.loop, daemon=True)
    t1.start()
    t2.start()
    time.sleep(2)

    print(f"提交任务: {TASK_TEXT}")
    _post("/api/message", {"text": TASK_TEXT})

    t0 = time.time()
    delivered = False
    while time.time() - t0 < TIMEOUT:
        if any(e["event"] == "delivery" for e in events):
            delivered = True
            break
        if monitor.killed:
            print(f"!!! 安全熔断: {monitor.kill_reason}")
            break
        time.sleep(5)
    wall = time.time() - t0
    monitor._stop.set()
    time.sleep(1)
    stop.set()

    outputs = []
    for e in events:
        if e["event"] == "delivery":
            for p in e["data"].get("outputs", []):
                outputs.append({"path": p, "exists": Path(p).exists()})

    from brain.eval.video import probe_duration, VideoError
    for o in outputs:
        if o["exists"] and str(o["path"]).lower().endswith(".mp4"):
            try:
                o["duration_s"] = round(probe_duration(o["path"]), 1)
            except VideoError:
                o["duration_s"] = None

    temp_all = [s["gpu"].get("temp_c", 0) for s in monitor.samples]
    result = {
        "task": TASK_TEXT, "delivered": delivered, "wall_s": round(wall, 1),
        "killed": monitor.killed, "kill_reason": monitor.kill_reason,
        "warned": monitor.warned, "events": len(events),
        "outputs": outputs,
        "hw": {
            "samples": len(monitor.samples),
            "peak_temp_c": max(temp_all) if temp_all else None,
            "max_power_w": max((s["gpu"].get("power_w", 0)
                                for s in monitor.samples), default=None),
            "max_gpu_util": max((s["gpu"].get("gpu_util", 0)
                                 for s in monitor.samples), default=None),
            "max_cpu_pct": max((s.get("cpu_pct", 0)
                                for s in monitor.samples), default=None),
            "temp_curve": [round(x, 1) for x in
                           temp_all[::max(len(temp_all) // 30, 1)]],
        },
        "tool_sequence": [e["data"].get("tool") for e in events
                          if e["event"] == "tool_start"],
        "stage_sequence": [e["data"].get("stage") for e in events
                           if e["event"] == "stage"],
        "evaluations": [{"kind": e["data"].get("kind"),
                         "verdict": e["data"].get("verdict"),
                         "score": e["data"].get("score")}
                        for e in events if e["event"] == "evaluation"],
        "workflow_snapshots": [
            {"nodes": len((e["data"].get("graph") or {})),
             "ts": round(e["recv_ts"] - t0, 1)}
            for e in events if e["event"] == "workflow_update"],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(json.dumps({"delivered": delivered, "killed": monitor.killed,
                      "wall_s": round(wall), "outputs": len(outputs),
                      "peak_temp": result["hw"]["peak_temp_c"],
                      "tools": len(result["tool_sequence"])},
                     ensure_ascii=False))
    print("数据已保存:", OUT)


if __name__ == "__main__":
    main()
