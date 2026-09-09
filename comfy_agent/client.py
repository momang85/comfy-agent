# -*- coding: utf-8 -*-
"""ComfyUI HTTP 客户端（纯标准库 urllib）。

安全约束：仅允许连接本机回环地址上的 ComfyUI（默认 127.0.0.1:8188），
禁止重定向跟随，杜绝 SSRF 风险。若需连接局域网内其他 ComfyUI 实例，
设置环境变量 COMFY_ALLOW_LAN=1 并自行承担网络风险。
"""
from __future__ import annotations

import ipaddress
import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Optional

from . import config


class ComfyUIError(Exception):
    """带结构化信息的 ComfyUI 错误。"""

    def __init__(self, message: str, payload: Optional[dict] = None):
        super().__init__(message)
        self.payload = payload or {}


def _assert_loopback_url(url: str) -> None:
    """SSRF 防护：只允许 http(s) 协议 + 回环地址（或显式放行的局域网地址）。"""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ComfyUIError(f"不允许的协议: {parsed.scheme}（仅 http/https）", {})
    if parsed.username or parsed.password:
        raise ComfyUIError("URL 中不允许携带凭据", {})
    hostname = parsed.hostname or ""
    allow_lan = config.COMFY_ALLOW_LAN
    try:
        infos = socket.getaddrinfo(hostname, parsed.port or 80,
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise ComfyUIError(f"无法解析主机名: {hostname}（{e}）", {}) from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        ok = ip.is_loopback or (allow_lan and (ip.is_private or ip.is_global))
        if not ok:
            raise ComfyUIError(
                f"安全限制：目标 {ip} 不是回环地址。"
                "comfy-agent 默认只连接本机 ComfyUI（127.0.0.1）。"
                "如确需连接其他地址，设置环境变量 COMFY_ALLOW_LAN=1。", {})


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(newurl, code, "重定向已被阻止", headers, fp)


class Client:
    def __init__(self, base_url: str = None):
        self.base = (base_url or config.COMFY_URL).rstrip("/")
        _assert_loopback_url(self.base)
        self._opener = urllib.request.build_opener(_NoRedirect)

    # ---------- 基础 ----------
    def _request(self, method: str, path: str, body: Any = None,
                 timeout: int = None, raw: bool = False, headers: dict = None):
        url = self.base + path
        _assert_loopback_url(url)
        data = None
        hdrs = {"Accept": "application/json"}
        if headers:
            hdrs.update(headers)
        if body is not None:
            if isinstance(body, (bytes, bytearray)):
                data = bytes(body)
            else:
                data = json.dumps(body).encode("utf-8")
                hdrs["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
        try:
            with self._opener.open(req, timeout=timeout or config.HTTP_TIMEOUT) as resp:
                content = resp.read()
                if raw:
                    return content
                return json.loads(content) if content else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            try:
                payload = json.loads(detail)
            except Exception:
                payload = {"raw": detail}
            raise ComfyUIError(f"HTTP {e.code} {method} {path}: {detail[:800]}", payload) from e
        except urllib.error.URLError as e:
            raise ComfyUIError(
                f"无法连接 ComfyUI（{self.base}）：{e.reason}。"
                "请先通过绘世启动器启动 ComfyUI。", {}) from e

    def get(self, path: str, **kw):
        return self._request("GET", path, **kw)

    def post(self, path: str, body: Any = None, **kw):
        return self._request("POST", path, body=body, **kw)

    # ---------- 服务器状态 ----------
    def system_stats(self) -> dict:
        """系统状态：ComfyUI 版本、GPU 名称/显存/空闲显存。"""
        return self.get("/system_stats")

    def vram_free_gb(self) -> float:
        try:
            for dev in self.system_stats().get("devices", []):
                return dev.get("vram_free", 0) / (1024 ** 3)
        except Exception:
            pass
        return 0.0

    # ---------- 模型与节点 ----------
    def models(self, folder: str = None) -> dict | list:
        """模型清单。folder=None 返回 {文件夹: [文件名]}（0.3x 服务器：
        /models 返回文件夹名列表，/models/{folder} 返回该目录内容）；
        传 folder 只返回该目录的文件名列表。"""
        data = self.get("/models")
        if isinstance(data, list) and data and isinstance(data[0], str):
            # 0.3x：文件夹名列表
            if folder is not None:
                entries = self.get(f"/models/{urllib.parse.quote(folder)}")
                # 该端点返回 [{name, type, ...}]，取文件名
                return [e.get("name") if isinstance(e, dict) else e for e in entries]
            mapping = {}
            for f in data:
                try:
                    entries = self.get(f"/models/{urllib.parse.quote(f)}")
                    mapping[f] = [e.get("name") if isinstance(e, dict) else e
                                  for e in entries]
                except ComfyUIError:
                    mapping[f] = []
            return mapping
        if isinstance(data, list):
            # 旧形态：[{name: folder, models: [...]}]
            mapping = {entry.get("name", ""): entry.get("models", []) for entry in data
                       if isinstance(entry, dict)}
            if folder is not None:
                return mapping.get(folder, [])
            return mapping
        # 字典形态
        if folder is not None:
            return data.get(folder, [])
        return data

    def object_info(self, node_class: str = None) -> dict:
        """节点签名（pin 级 schema）。传 node_class 只取一个节点。"""
        path = "/object_info" + (f"/{urllib.parse.quote(node_class)}" if node_class else "")
        return self.get(path)

    def queue(self) -> dict:
        return self.get("/queue")

    def interrupt(self) -> dict:
        return self.post("/interrupt", {})

    def free(self, unload_models: bool = True, free_memory: bool = True) -> dict:
        """卸载模型/释放显存（评估前调用）。"""
        return self.post("/free", {"unload_models": unload_models, "free_memory": free_memory})

    # ---------- 文件 ----------
    def upload_image(self, file_path: str | Path, image_type: str = "input",
                     overwrite: bool = True) -> dict:
        """上传本地图片到 /input（multipart 表单）。返回服务器端文件名。"""
        p = Path(file_path)
        if not p.exists():
            raise ComfyUIError(f"文件不存在: {p}", {})
        boundary = "----comfyagent" + uuid.uuid4().hex
        name = p.name
        # 中文/特殊文件名转 ASCII 安全名，避免服务器端编码问题
        safe = "".join(c if c.isascii() and (c.isalnum() or c in ".-_") else "_"
                       for c in name) or "upload.png"
        parts = []
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="overwrite"\r\n\r\n'
                     f'{"true" if overwrite else "false"}\r\n'.encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="type"\r\n\r\n'
                     f'{image_type}\r\n'.encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="image"; '
                     f'filename="{safe}"\r\nContent-Type: application/octet-stream\r\n\r\n'
                     .encode() + p.read_bytes() + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        body = b"".join(parts)
        return self._request(
            "POST", "/upload/image", body=body, raw=False,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )

    def view(self, filename: str, subfolder: str = "", folder_type: str = "output",
             save_to: str | Path = None) -> Any:
        """取回输出文件。save_to 给定时下载到本地并返回路径，否则返回 bytes。"""
        q = urllib.parse.urlencode({
            "filename": filename,
            "subfolder": subfolder,
            "type": folder_type,
        })
        data = self._request("GET", f"/view?{q}", raw=True, timeout=300)
        if save_to:
            dest = Path(save_to)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            return str(dest)
        return data

    # ---------- 工作流执行 ----------
    def prompt(self, workflow_api: dict) -> dict:
        """提交 API 格式工作流。服务端先校验：失败抛 ComfyUIError（含 node_errors）。
        成功返回 {"prompt_id": ..., "number": ..., "node_errors": {}}。"""
        return self.post("/prompt", {"prompt": workflow_api, "client_id": _client_id()})

    def history(self, prompt_id: str = None) -> dict:
        if prompt_id:
            return self.get(f"/history/{urllib.parse.quote(prompt_id)}")
        return self.get("/history")

    def wait_for_result(self, prompt_id: str, timeout: float = 1800.0,
                        on_status=None) -> dict:
        """轮询直到完成。返回该 prompt 的 history 条目。
        on_status(status_dict) 每次轮询回调（用于进度展示）。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            hist = self.history(prompt_id)
            entry = hist.get(prompt_id)
            if entry is not None:
                status = entry.get("status", {})
                if status.get("completed") or status.get("status_str") == "error":
                    return entry
            if on_status:
                try:
                    on_status({"pending": True, "pid": prompt_id})
                except Exception:
                    pass
            time.sleep(config.POLL_INTERVAL)
        raise ComfyUIError(f"等待超时（{timeout}s）：{prompt_id}", {})

    # ---------- 便捷 ----------
    def outputs_of(self, history_entry: dict, save_dir: str | Path = None) -> list[dict]:
        """从 history 条目提取全部输出文件；save_dir 给定时下载并附 local_path。"""
        results = []
        for node_out in history_entry.get("outputs", {}).values():
            for images in node_out.get("images", []):
                item = dict(images)
                if save_dir:
                    sub = item.get("subfolder", "")
                    local = Path(save_dir) / (
                        f"{sub}_{item['filename']}" if sub else item["filename"])
                    item["local_path"] = self.view(
                        item["filename"], sub, item.get("type", "output"), save_to=local)
                results.append(item)
            # 视频节点可能输出 gifs / videos / audio
            for key in ("gifs", "videos", "audio"):
                for f in node_out.get(key, []):
                    item = dict(f)
                    item.setdefault("kind", key)
                    if save_dir:
                        sub = item.get("subfolder", "")
                        local = Path(save_dir) / (
                            f"{sub}_{item['filename']}" if sub else item["filename"])
                        item["local_path"] = self.view(
                            item["filename"], sub, item.get("type", "output"), save_to=local)
                    results.append(item)
        return results


def _client_id() -> str:
    return uuid.uuid4().hex
