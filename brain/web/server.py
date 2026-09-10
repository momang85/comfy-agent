# -*- coding: utf-8 -*-
"""内置 Web UI 服务器（纯标准库，仅绑定 127.0.0.1）。

- GET  /events              SSE 事件流（带 project 标签，前端按当前项目过滤）
- GET  /api/status?project= 状态快照（当前项目草稿图/产物列表(mtime降序)/显存）
- GET  /api/projects        项目列表；POST /api/projects {name} 新建
- POST /api/message {text, project_id, image?}   用户输入 → 该项目 Brain 工作线程
- POST /api/upload  {project_id, name, data(base64)}  传图 → 落盘项目 uploads/ 并上传 ComfyUI /input
- POST /api/interrupt       中断当前任务
- GET  /api/outputs/*       产物文件（路径白名单防穿越）
- GET  / /app.js /style.css 静态页
"""
from __future__ import annotations

import base64
import json
import os
import queue
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from comfy_agent import config
from .. import events as ev
from ..events import project_context
from ..projects import ProjectStore, PROJECTS_ROOT

STATIC_DIR = Path(__file__).parent / "static"
HOST, PORT = "127.0.0.1", 8899
_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")

# 图片上传限制与格式白名单（magic bytes 校验，不信任扩展名）
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
IMAGE_MAGICS = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"RIFF", ".webp"),        # RIFF....WEBP，再检查偏移 8 处的 WEBP 标记
)
ALLOWED_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


def safe_upload_name(name: str) -> str:
    """上传文件名清洗：ASCII 安全名 + 扩展名白名单（与 client.upload_image 同规则）。"""
    p = Path(name or "upload.png")
    suffix = p.suffix.lower()
    if suffix not in ALLOWED_IMAGE_SUFFIXES:
        suffix = ".png"
    stem = p.stem
    safe = "".join(c if c.isascii() and (c.isalnum() or c in ".-_") else "_"
                   for c in stem) or "upload"
    return safe[:80] + suffix


def validate_image_bytes(raw: bytes) -> tuple[bool, str]:
    """图片字节校验：大小 + magic bytes。返回 (ok, 原因)。"""
    if not raw:
        return False, "空文件"
    if len(raw) > MAX_UPLOAD_BYTES:
        return False, f"超过大小上限 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB"
    for magic, _suffix in IMAGE_MAGICS:
        if raw.startswith(magic):
            if magic == b"RIFF" and raw[8:12] != b"WEBP":
                continue
            return True, ""
    return False, "仅支持 PNG/JPEG/WebP 图片"



class BrainSession:
    """一个项目的 Brain + 输入队列 + 当前阶段。"""

    def __init__(self, project):
        from ..agent import Brain
        self.project = project
        self.brain = Brain(verbose=True, project=project)
        self.inbox: queue.Queue = queue.Queue()
        self.stage = "idle"
        self.last_error: str | None = None
        self.worker = threading.Thread(target=self._run, daemon=True)
        self.worker.start()

    def _run(self):
        while True:
            try:
                item = self.inbox.get(timeout=0.5)
            except queue.Empty:
                continue
            if isinstance(item, dict):
                text, image = item.get("text", ""), item.get("image")
            else:
                text, image = item, None
            self.stage = "thinking"
            self.last_error = None
            # 项目上下文：本线程内所有事件自动打上 project 标签
            with project_context(self.project.id):
                ev.emit("stage", {"stage": "thinking", "detail": {}})
                try:
                    self.brain.handle(text, image=image)
                except Exception as e:
                    # 单条消息出错不得打死会话线程：曾因日志字符编码异常
                    # 线程静默死亡，项目永久停在 thinking、后续消息只入队
                    import traceback
                    tb = traceback.format_exc()
                    self.last_error = f"{type(e).__name__}: {e}"
                    try:
                        print(f"[session:{self.project.id}] {self.last_error}\n"
                              f"{tb[-1200:]}", flush=True)
                    except Exception:
                        pass
                    ev.emit("error", {"message": self.last_error,
                                      "traceback": tb[-1200:]})
                finally:
                    self.stage = "idle"
                    ev.emit("stage", {"stage": "idle", "detail": {}})


class WebSession:
    def __init__(self):
        self.store = ProjectStore()
        self.default_id = self.store.ensure_default().id
        self.sessions: dict[str, BrainSession] = {}
        self._lock = threading.Lock()

    def session_for(self, pid: str | None) -> BrainSession:
        pid = pid or self.default_id
        if not self.store.get(pid):
            pid = self.default_id
        with self._lock:
            if pid not in self.sessions:
                self.sessions[pid] = BrainSession(self.store.get(pid))
            return self.sessions[pid]

    def start_monitors(self):
        t = threading.Thread(target=self._vram_poller, daemon=True)
        t.start()
        t2 = threading.Thread(target=self._queue_poller, daemon=True)
        t2.start()

    def _vram_poller(self):
        while True:
            try:
                s = self.session_for(None)
                stats = s.brain.ctx.client.system_stats()
                for d in stats.get("devices", []):
                    ev.emit("vram", {
                        "total_gb": round(d.get("vram_total", 0) / 1e9, 1),
                        "free_gb": round(d.get("vram_free", 0) / 1e9, 1),
                        "name": d.get("name", "")[:30]})
            except Exception:
                pass
            time.sleep(3)

    def _queue_poller(self):
        while True:
            try:
                s = self.session_for(None)
                q = s.brain.ctx.client.queue()
                running = q.get("queue_running") or []
                pending = q.get("queue_pending") or []
                if running or pending:
                    pid = running[0][1] if running else pending[0][1]
                    ev.emit("progress", {
                        "prompt_id": pid[:8],
                        "queue_position": len(running) + len(pending)})
            except Exception:
                pass
            time.sleep(2)


SESSION: WebSession | None = None


def get_settings() -> dict:
    """当前生效配置（key 脱敏）+ settings.json 中已保存的字段。"""
    from comfy_agent import config as _cfg
    from ..llm import LLMClient
    s = _cfg.load_user_settings()
    masked = LLMClient().masked()
    return {"ok": True,
            "effective": masked,
            "saved": {k: (v[:4] + "…" + v[-4:] if k.endswith("_key")
                          and len(v) > 8 else v)
                      for k, v in s.items()}}


def status_snapshot(project_id: str | None) -> dict:
    """项目状态快照（产物按 mtime 降序，最新在前）。"""
    s = SESSION
    if s is None:
        return {"ok": False, "error": "服务初始化中"}
    bs = s.session_for(project_id)
    ctx = bs.brain.ctx
    out_root = bs.project.outputs_dir()
    outputs = []
    if out_root.exists():
        files = [f for f in out_root.rglob("*") if f.is_file()
                 and f.suffix.lower() in
                 (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm")]
        files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        now = time.time()
        for f in files[:32]:
            # 相对项目根（含 outputs/ 段），与 /api/outputs/<pid>/<rel> 对齐
            rel = str(f.relative_to(bs.project.dir)).replace("\\", "/")
            outputs.append({"path": "/api/outputs/" + bs.project.id + "/" + rel,
                            "name": f.name, "rel": rel,
                            "kind": "video" if f.suffix.lower()
                            in (".mp4", ".webm") else "image",
                            "new": (now - f.stat().st_mtime) < 86400})
    return {"ok": True, "project": bs.project.to_dict(),
            "stage": bs.stage,
            "last_error": bs.last_error,
            "draft": ctx.draft, "draft_meta": ctx.draft_meta,
            "outputs": outputs}


def safe_output_path(rel: str):
    """产物文件路径校验：解析后必须落在 projects 根内（防穿越）。"""
    base = PROJECTS_ROOT.resolve()
    try:
        f = (base / rel).resolve()
    except (OSError, ValueError):
        return None
    if not str(f).startswith(str(base)) or not f.is_file():
        return None
    return f


def _decode_body(raw: bytes) -> str:
    """请求体解码：utf-8 优先，回退 gbk（Windows curl 等客户端）。"""
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "comfy-agent-web/1.0"

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        q = parse_qs(parsed.query)
        if path == "/":
            return self._serve_static("index.html", "text/html")
        if path in ("/app.js", "/style.css"):
            return self._serve_static(path[1:], "application/javascript"
                                      if path.endswith(".js") else "text/css")
        if path == "/events":
            return self._sse()
        if path == "/api/status":
            return self._json(status_snapshot(q.get("project", [None])[0]))
        if path == "/api/projects":
            return self._json({"ok": True,
                               "projects": [p.to_dict()
                                            for p in SESSION.store.list()]})
        if path == "/api/settings":
            return self._json(get_settings())
        if path.startswith("/api/outputs/"):
            return self._serve_output(path[len("/api/outputs/"):])
        self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/message":
            return self._post_message()
        if parsed.path == "/api/upload":
            return self._post_upload()
        if parsed.path == "/api/projects":
            return self._post_project()
        if parsed.path == "/api/settings":
            return self._post_settings()
        if parsed.path == "/api/interrupt":
            s = SESSION.session_for(None)
            s.brain.ctx.client.interrupt()
            return self._json({"ok": True})
        self.send_error(404)

    def _post_settings(self):
        """保存用户设置（settings.json 本地落盘，gitignored）+ 热生效。"""
        from comfy_agent import config as _cfg
        if SESSION is None:
            self.send_error(503, "服务初始化中")
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            data = json.loads(_decode_body(body))
        except json.JSONDecodeError:
            return self.send_error(400)
        allowed = ("llm_base_url", "llm_api_key", "llm_model",
                   "vlm_base_url", "vlm_api_key", "vlm_model")
        merged = _cfg.load_user_settings()
        for k in allowed:
            v = data.get(k)
            if v is not None and str(v).strip():
                merged[k] = str(v).strip()
        # model_prefs：图像模板的模型偏好（{"sdxl": "模型名", "sd15": "模型名"}）。
        # 朋友设备模型名不同时，模板按此优先绑定本机模型
        v = data.get("model_prefs")
        if isinstance(v, dict):
            prefs = {str(kk): str(vv).strip()
                     for kk, vv in v.items() if str(vv).strip()}
            if prefs:
                merged["model_prefs"] = prefs
        _cfg.save_user_settings(merged)
        # 热生效：所有项目的 Brain LLM 重读配置
        for bs in SESSION.sessions.values():
            try:
                bs.brain.llm.reload_settings()
            except Exception:
                pass
        return self._json({"ok": True, **get_settings()})

    def _serve_static(self, name: str, ctype: str):
        f = STATIC_DIR / name
        if not f.exists():
            return self.send_error(404)
        body = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # 禁缓存：前端升级后普通刷新即生效（否则旧 app.js 配新 API 路径会全图 404）
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data: dict, code: int = 200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_output(self, rel: str):
        f = safe_output_path(rel)
        if f is None:
            return self.send_error(404)
        size = f.stat().st_size
        ctype = "video/mp4" if f.suffix.lower() == ".mp4" else \
            "video/webm" if f.suffix.lower() == ".webm" else \
            "image/png" if f.suffix.lower() == ".png" else \
            "image/jpeg" if f.suffix.lower() in (".jpg", ".jpeg") else \
            "image/webp" if f.suffix.lower() == ".webp" else \
            "application/octet-stream"
        # Range 支持：视频播放/拖进度依赖 206 Partial Content
        rng = self.headers.get("Range")
        if rng:
            m = _RANGE_RE.match(rng)
            if m:
                start_s, end_s = m.group(1), m.group(2)
                if start_s == "" and end_s:            # bytes=-N（尾部）
                    start = max(0, size - int(end_s))
                    end = size - 1
                else:
                    start = int(start_s) if start_s else 0
                    end = int(end_s) if end_s else size - 1
                    end = min(end, size - 1)
                if start >= size or start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                length = end - start + 1
                self.send_response(206)
                self.send_header("Content-Range",
                                 f"bytes {start}-{end}/{size}")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(length))
                self.end_headers()
                with f.open("rb") as fh:
                    fh.seek(start)
                    self.wfile.write(fh.read(length))
                return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self.wfile.write(f.read_bytes())

    def _post_message(self):
        if SESSION is None:
            self.send_error(503, "服务初始化中")
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            data = json.loads(_decode_body(body))
            text = data.get("text", "").strip()
            project_id = data.get("project_id")
            image = data.get("image")
        except json.JSONDecodeError:
            return self.send_error(400)
        if not text and not image:
            return self.send_error(400)
        bs = SESSION.session_for(project_id)
        bs.inbox.put({"text": text, "image": image})
        return self._json({"ok": True, "project": bs.project.id})

    def _post_upload(self):
        """传图：base64 JSON → 校验 → 落盘项目 uploads/ → 上传 ComfyUI /input。"""
        if SESSION is None:
            self.send_error(503, "服务初始化中")
            return
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0 or length > MAX_UPLOAD_BYTES * 2:   # base64 膨胀余量
            self.send_error(413, "请求体过大")
            return
        body = self.rfile.read(length)
        try:
            data = json.loads(_decode_body(body))
            project_id = data.get("project_id")
            name = data.get("name", "upload.png")
            raw = base64.b64decode(data.get("data", ""), validate=True)
        except (json.JSONDecodeError, ValueError):
            return self.send_error(400, "请求体格式错误")
        ok, reason = validate_image_bytes(raw)
        if not ok:
            return self._json({"ok": False, "error": reason}, code=400)
        bs = SESSION.session_for(project_id)
        fname = f"{uuid.uuid4().hex[:8]}_{safe_upload_name(name)}"
        local = bs.project.uploads_dir() / fname
        local.write_bytes(raw)
        server_name = None
        try:
            server_name = bs.brain.ctx.client.upload_image(local).get("name")
        except Exception as e:
            local.unlink(missing_ok=True)
            return self._json({"ok": False, "error": f"上传 ComfyUI 失败: {e}"},
                              code=502)
        url = f"/api/outputs/{bs.project.id}/uploads/{fname}"
        with project_context(bs.project.id):
            ev.emit("user_upload", {"url": url, "name": fname,
                                    "server_name": server_name})
        return self._json({"ok": True, "url": url, "name": fname,
                           "local_path": str(local),
                           "server_name": server_name})

    def _post_project(self):
        if SESSION is None:
            self.send_error(503, "服务初始化中")
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            name = json.loads(_decode_body(body)).get("name", "").strip()
        except json.JSONDecodeError:
            return self.send_error(400)
        if not name or len(name) > 40:
            return self.send_error(400)
        p = SESSION.store.create(name)
        return self._json({"ok": True, "project": p.to_dict()})

    def _sse(self):
        q: queue.Queue = queue.Queue()

        def forward(msg):
            q.put(json.dumps(msg, ensure_ascii=False, default=str))

        ev.bus.subscribe(forward)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            while True:
                try:
                    payload = q.get(timeout=15)
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            ev.bus.unsubscribe(forward)

    def log_message(self, *args):
        pass


def serve(port: int = PORT, open_browser: bool = True):
    global SESSION
    SESSION = WebSession()
    SESSION.start_monitors()
    # PID 文件：供一键启动脚本精确识别/清理本服务进程
    pidfile = config.AGENT_HOME / "webui.pid"
    try:
        pidfile.write_text(str(os.getpid()), encoding="ascii")
    except OSError:
        pass
    httpd = ThreadingHTTPServer((HOST, port), Handler)
    url = f"http://{HOST}:{port}"
    print(f"ComfyUI 大脑 Web UI: {url}（Ctrl+C 退出）")
    if open_browser:
        try:
            import webbrowser
            threading.Timer(1.0, lambda: webbrowser.open(url)).start()
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        try:
            pidfile.unlink(missing_ok=True)
        except OSError:
            pass
