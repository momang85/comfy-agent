# -*- coding: utf-8 -*-
"""缺模型搜索与下载（纯标准库，无第三方依赖）。

三道安全边界，缺一不可：

1. **出网**：仅 http/https，host 必须是公网地址（拒绝 localhost/回环/私有/
   保留/多播/未指定/内嵌凭据）；重定向**逐跳复校验**，最多 4 跳。
   与 client.py 的回环白名单相反——那里只连本机 ComfyUI，这里只连公网。
2. **落盘**：folder 与 filename 逐段校验（禁 `..`/盘符/绝对路径/空段/保留名），
   最终路径 resolve 后必须仍在 models 根内；后缀必须在模型文件白名单内。
3. **执行**：后台线程 + `.part` 临时文件 → 校验通过后再 os.replace 原子落位，
   下载中可取消；校验不通过的文件绝不进入 models 目录。

对外接口：
  search_models(filename, folder=None, vram_free_gb=None, offline=False) -> list
  MANAGER.request(url, filename, folder, ...) -> dict   # 登记后立即返回
  MANAGER.confirm(did) / decline(did) / cancel(did) / get(did) / active()
  bind_emitter(fn) / bind_finish_hook(fn)               # 由 Web 层接入
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import config

# ---------------- 常量 ----------------

# 允许落盘的 models 子目录（未知目录需已存在于磁盘，见 _check_folder）
MODEL_FOLDERS = frozenset((
    "checkpoints", "loras", "vae", "vae_approx", "clip", "clip_vision",
    "text_encoders", "controlnet", "diffusion_models", "unet",
    "upscale_models", "embeddings", "style_models", "ipadapter",
    "audio_encoders", "model_patches", "animatediff_models",
    "animatediff_motion_lora", "gligen", "hypernetworks", "photomaker",
    "instantid", "sams", "ultralytics", "diffusers", "llm_gguf",
))

MODEL_EXTS = frozenset((
    ".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".sft",
    ".onnx", ".pkl", ".engine", ".patch",
))

# Manager 目录里的 type/save_path → models 子目录
TYPE_FOLDER = {
    "checkpoint": "checkpoints", "checkpoints": "checkpoints",
    "lora": "loras", "loras": "loras", "locon": "loras", "lycoris": "loras",
    "vae": "vae", "vae_approx": "vae_approx",
    "controlnet": "controlnet", "clip": "text_encoders",
    "clip_vision": "clip_vision", "text_encoder": "text_encoders",
    "unet": "diffusion_models", "diffusion_model": "diffusion_models",
    "upscale": "upscale_models", "upscale_model": "upscale_models",
    "embedding": "embeddings", "embeddings": "embeddings",
    "gligen": "gligen", "hypernetwork": "hypernetworks",
    "audio_encoder": "audio_encoders",
}

_BAD_SEGMENTS = frozenset(("", ".", ".."))
_RESERVED_NAMES = frozenset(
    ("con", "prn", "aux", "nul")
    + tuple(f"com{i}" for i in range(1, 10))
    + tuple(f"lpt{i}" for i in range(1, 10))
)

_MAX_HOPS = 4
CHUNK = 1024 * 1024                 # 1MB 分块
DEFAULT_MAX_GB = 40.0
DISK_HEADROOM = 1024 ** 3           # 落盘后至少留 1GB
_UA = "comfy-agent/1.0 (local model downloader)"

# 搜索源（服务端点常量，非凭据）
HF_API = "https://huggingface.co"
HF_MIRROR = "https://hf-mirror.com"
CIVITAI_API = "https://civitai.com"
MODELSCOPE_API = "https://www.modelscope.cn"


class DownloadError(RuntimeError):
    """下载流程通用错误（大脑据此降级）。"""


class UrlNotAllowed(DownloadError):
    """URL 不是公网 http/https 地址。"""


class PathNotAllowed(DownloadError):
    """目标路径不合法或越出 models 目录。"""


class Canceled(DownloadError):
    """用户取消。"""


# ---------------- URL 校验（安全边界 1） ----------------

def _resolve_ips(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    return [i[4][0] for i in infos]


def assert_public_url(url: str, resolver=None) -> str:
    """校验并返回 url；非公网 http/https 一律拒绝。

    resolver 可注入（测试用），默认 DNS 解析。
    """
    text = str(url or "").strip()
    parts = urllib.parse.urlsplit(text)
    if parts.scheme not in ("http", "https"):
        raise UrlNotAllowed(
            f"仅允许 http/https 地址，收到 {parts.scheme or '(空)'}")
    if parts.username or parts.password:
        raise UrlNotAllowed("地址不得内嵌用户名/密码")
    host = parts.hostname
    if not host:
        raise UrlNotAllowed("地址缺少主机名")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as e:
        raise UrlNotAllowed(f"端口非法：{e}") from e
    resolve = resolver or _resolve_ips
    try:
        ips = resolve(host, port)
    except (OSError, ValueError) as e:
        raise UrlNotAllowed(f"域名解析失败：{host}（{e}）") from e
    if not ips:
        raise UrlNotAllowed(f"域名解析为空：{host}")
    for raw in ips:
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError as e:
            raise UrlNotAllowed(f"无法识别的地址 {raw!r}") from e
        if (ip.is_loopback or ip.is_private or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified
                or not ip.is_global):
            raise UrlNotAllowed(f"拒绝非公网地址：{host} -> {raw}")
    return text


class _ValidatingRedirect(urllib.request.HTTPRedirectHandler):
    """跟随重定向但每跳复校验（防公网地址 302 到内网）。"""

    max_redirections = _MAX_HOPS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        assert_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(_ValidatingRedirect())


def _open_stream(url: str, timeout: int = 30,
                 headers: dict | None = None):
    """打开可流式读取的响应（调用方负责 close）。"""
    assert_public_url(url)
    hdrs = {"User-Agent": _UA}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, headers=hdrs)
    return _opener().open(req, timeout=timeout)


def _json(url: str, timeout: int = 20):
    with _open_stream(url, timeout=timeout) as resp:
        raw = resp.read(4 * 1024 * 1024)
    return json.loads(raw.decode("utf-8", "replace"))


def _head_size(url: str, timeout: int = 20) -> int:
    """探测远端文件大小；失败返回 0（视为该地址无此文件）。"""
    try:
        req = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": _UA})
        with _opener().open(req, timeout=timeout) as resp:
            n = int(resp.headers.get("Content-Length") or 0)
            if n > 0:
                return n
    except urllib.error.HTTPError as e:
        e.close()          # 不关会在 GC 时报 ResourceWarning（实测 401/404）
    except Exception:
        pass
    # 部分站点不支持 HEAD：用 Range 取首字节，读 Content-Range 总长
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": _UA, "Range": "bytes=0-0"})
        with _opener().open(req, timeout=timeout) as resp:
            cr = resp.headers.get("Content-Range") or ""
            m = re.search(r"/(\d+)\s*$", cr)
            if m:
                return int(m.group(1))
            return int(resp.headers.get("Content-Length") or 0)
    except urllib.error.HTTPError as e:
        e.close()
        return 0
    except Exception:
        return 0


# ---------------- 大小解析 ----------------

_SIZE_UNITS = {
    "b": 1, "kb": 1024, "k": 1024, "kib": 1024,
    "mb": 1024 ** 2, "m": 1024 ** 2, "mib": 1024 ** 2,
    "gb": 1024 ** 3, "g": 1024 ** 3, "gib": 1024 ** 3,
    "tb": 1024 ** 4, "t": 1024 ** 4, "tib": 1024 ** 4,
}


def parse_size(text) -> int:
    """'3.53GB' / '4.71MB' / 12345 -> 字节数；无法识别返回 0。"""
    if isinstance(text, bool):
        return 0
    if isinstance(text, (int, float)):
        return int(text)
    m = re.match(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([a-zA-Z]*)\s*$", str(text or ""))
    if not m:
        return 0
    val = float(m.group(1))
    unit = (m.group(2) or "b").lower()
    if unit in _SIZE_UNITS:
        return int(val * _SIZE_UNITS[unit])
    return 0


def human_size(n) -> str:
    try:
        val = float(n or 0)
    except (TypeError, ValueError):
        return "未知"
    if val <= 0:
        return "未知"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if val < 1024 or unit == "TB":
            return f"{int(val)} B" if unit == "B" else f"{val:.2f} {unit}"
        val /= 1024
    return f"{val:.2f} TB"


# ---------------- 路径校验（安全边界 2） ----------------

def models_root() -> Path:
    root = getattr(config, "COMFY_ROOT", "") or ""
    if not str(root).strip():
        raise PathNotAllowed(
            "未配置 ComfyUI 安装目录（设置 COMFY_ROOT 或运行 install.bat）")
    return Path(config.MODELS_DIR)


def _clean_segments(value, what: str, max_depth: int) -> list[str]:
    """逐段校验路径片段：禁 .. / 盘符 / 绝对路径 / 空段 / 保留名。"""
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        raise PathNotAllowed(f"{what}不能为空")
    if "\x00" in text:
        raise PathNotAllowed(f"{what}含非法字符")
    if ":" in text:
        raise PathNotAllowed(f"{what}不得包含盘符或冒号")
    parts = [s.strip() for s in text.split("/")]
    if len(parts) > max_depth:
        raise PathNotAllowed(f"{what}层级过深（最多 {max_depth} 层）")
    for seg in parts:
        if seg in _BAD_SEGMENTS or seg.startswith("."):
            raise PathNotAllowed(f"{what}含非法片段 {seg!r}")
        if seg.split(".")[0].lower() in _RESERVED_NAMES:
            raise PathNotAllowed(f"{what}使用了系统保留名 {seg!r}")
    return parts


def _assert_inside(path: Path, root: Path) -> Path:
    root_r = Path(root).resolve()
    try:
        p = Path(path).resolve()
    except OSError as e:
        raise PathNotAllowed(f"路径无法解析：{path}（{e}）") from e
    if p == root_r or root_r not in p.parents:
        raise PathNotAllowed(f"目标路径越出 models 目录：{p}")
    return p


def _check_folder(folder: str) -> str:
    seg = _clean_segments(folder, "models 子目录", max_depth=1)[0]
    if seg in MODEL_FOLDERS:
        return seg
    if (models_root() / seg).is_dir():
        return seg          # 自定义节点自建的目录
    raise PathNotAllowed(
        f"未知的模型目录 {seg!r}；可用：{', '.join(sorted(MODEL_FOLDERS))}"
        "（自定义目录需已存在于 models/ 下）")


def target_path(folder: str, filename: str) -> Path:
    """校验并返回落盘的绝对路径（一定在 models 根内）。"""
    root = models_root()
    fparts = _check_folder(folder)
    nparts = _clean_segments(filename, "文件名", max_depth=3)
    name = nparts[-1]
    ext = Path(name).suffix.lower()
    if ext not in MODEL_EXTS:
        raise PathNotAllowed(
            f"仅允许模型文件后缀（{'/'.join(sorted(MODEL_EXTS))}），"
            f"收到 {name!r}")
    return _assert_inside(root.joinpath(fparts, *nparts), root)


def folder_hint(save_path: str, mtype: str = "", filename: str = "") -> str:
    """由 Manager 的 save_path/type 推断 models 子目录（仅作建议）。"""
    seg = str(save_path or "").replace("\\", "/").split("/")[0].strip()
    if seg.lower() in MODEL_FOLDERS:
        return seg.lower()
    t = str(mtype or "").strip().lower()
    if t in TYPE_FOLDER:
        return TYPE_FOLDER[t]
    ext = Path(str(filename or "")).suffix.lower()
    if ext in (".safetensors", ".ckpt"):
        return "checkpoints"
    return ""


def machine_fit(size: int, vram_free_gb: float | None = None) -> dict:
    """本机适配评估：磁盘空间 + 单文件上限（+ 显存提示，仅建议不阻断）。"""
    notes: list[str] = []
    fits = True
    try:
        free = shutil.disk_usage(str(models_root())).free
    except (OSError, PathNotAllowed):
        free = 0
    max_bytes = int(float(os.environ.get("MODEL_DOWNLOAD_MAX_GB",
                                         DEFAULT_MAX_GB)) * 1024 ** 3)
    if size and size > max_bytes:
        fits = False
        notes.append(f"超过单文件上限 {human_size(max_bytes)}"
                     "（可用 MODEL_DOWNLOAD_MAX_GB 调整）")
    if size and free and size + DISK_HEADROOM > free:
        fits = False
        notes.append(f"磁盘空间不足：需 {human_size(size)}，可用 {human_size(free)}")
    if vram_free_gb and size:
        # 只看文件体积与显存量级的关系，给建议不阻断（ComfyUI 会自动分块）
        if size > float(vram_free_gb) * 1024 ** 3:
            notes.append(f"文件大于空闲显存 {vram_free_gb:.1f}GB，"
                         "加载需分块（首次加载会明显变慢）")
    return {"fits": fits, "disk_free": free, "disk_free_text": human_size(free),
            "max_gb": round(max_bytes / 1024 ** 3, 1), "notes": notes}


# ---------------- 搜索源 ----------------

def _cand(url, filename, folder, source, *, size=0, size_text="",
          name="", reference="", mtype="", exact=False) -> dict:
    return {"url": url, "filename": filename, "folder": folder,
            "source": source, "size": int(size or 0),
            "size_text": size_text or human_size(size),
            "name": name or filename, "reference": reference,
            "type": mtype, "exact": bool(exact)}


def _score_filename(want: str, got: str) -> int:
    """0=同名, 1=同族（前缀关系）, 2=大概率同版本变体, 3=不相关（不采用）。

    子串匹配曾把 "definitely-not-a-real-model-xyz" 判为命中 "model.safetensors"，
    导致搜不到时返回一堆无关候选；改用"短名足够长 + 公共前缀占比高"的判据。
    """
    a, b = want.lower(), got.lower()
    sa, sb = Path(a).stem, Path(b).stem
    if a == b or sa == sb:
        return 0
    if sa.startswith(sb) or sb.startswith(sa):
        return 1
    short, long_ = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
    if len(short) >= 6:
        common = 0
        for x, y in zip(short, long_):
            if x != y:
                break
            common += 1
        if common >= 8 and common >= 0.6 * len(short):
            return 2
    return 3


def _from_manager_cache(filename: str, folder: str | None) -> list[dict]:
    """本机 ComfyUI-Manager 模型目录（离线，含真实 url/size/save_path）。"""
    cache = Path(getattr(config, "MANAGER_CACHE", "") or "")
    out: list[dict] = []
    if not cache.is_dir():
        return out
    for f in sorted(cache.glob("*model-list.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for it in (data.get("models") or []):
            if not isinstance(it, dict):
                continue
            fn = str(it.get("filename") or "").strip()
            url = str(it.get("url") or "").strip()
            if not fn or not url:
                continue
            rank = _score_filename(filename, fn)
            if rank >= 3:
                continue
            fdir = folder_hint(it.get("save_path"), it.get("type"), fn)
            if folder and fdir and fdir != folder:
                continue
            try:
                assert_public_url(url)
            except UrlNotAllowed:
                continue
            size = parse_size(it.get("size"))
            out.append(_cand(
                url, fn, fdir or (folder or ""), "manager", size=size,
                size_text=str(it.get("size") or ""), name=str(it.get("name") or ""),
                reference=str(it.get("reference") or ""),
                mtype=str(it.get("type") or ""), exact=(rank == 0)))
    out.sort(key=lambda c: (not c["exact"], c["filename"].lower()))
    return out


def _from_hf(filename: str, mirror: bool) -> list[dict]:
    """HuggingFace / hf-mirror：先搜仓库，再 HEAD 确认仓库里确有该文件名。"""
    base = HF_MIRROR if mirror else HF_API
    stem = Path(filename).stem
    if not stem:
        return []
    data = _json(f"{base}/api/models?search={urllib.parse.quote(stem)}&limit=8")
    out: list[dict] = []
    for m in (data if isinstance(data, list) else []):
        repo = m.get("modelId") or m.get("id")
        if not repo:
            continue
        cand_url = f"{base}/{repo}/resolve/main/{urllib.parse.quote(filename)}"
        size = _head_size(cand_url)
        if size <= 0:
            continue
        out.append(_cand(cand_url, filename, "", 
                         "hf-mirror" if mirror else "hf", size=size,
                         name=repo, reference=f"{base}/{repo}",
                         exact=True))
    return out


def _from_civitai(filename: str) -> list[dict]:
    stem = Path(filename).stem
    if not stem:
        return []
    data = _json(f"{CIVITAI_API}/api/v1/models?limit=5&query="
                 f"{urllib.parse.quote(stem)}")
    out: list[dict] = []
    for m in ((data or {}).get("items") or []):
        for v in (m.get("modelVersions") or []):
            for f in (v.get("files") or []):
                if _score_filename(filename, str(f.get("name") or "")) >= 3:
                    continue
                url = str(f.get("downloadUrl") or "").strip()
                if not url:
                    continue
                assert_public_url(url)      # 第三方给的地址逐条复校验
                size = int((f.get("sizeKB") or 0) * 1024)
                out.append(_cand(url, filename, "loras" if m.get("type") == "LORA"
                                 else "checkpoints", "civitai", size=size,
                                 name=str(m.get("name") or ""),
                                 reference=f"{CIVITAI_API}/models/{m.get('id')}",
                                 mtype=str(m.get("type") or ""), exact=True))
    return out


def _from_modelscope(filename: str) -> list[dict]:
    stem = Path(filename).stem
    if not stem:
        return []
    data = _json(f"{MODELSCOPE_API}/api/v1/models?PageSize=8&Name="
                 f"{urllib.parse.quote(stem)}")
    items = ((data or {}).get("Data") or {}).get("Models") or []
    out: list[dict] = []
    for m in items:
        repo = m.get("Path") or m.get("Name")
        if not repo:
            continue
        cand_url = (f"{MODELSCOPE_API}/api/v1/models/{repo}/repo?FilePath="
                    f"{urllib.parse.quote(filename)}")
        size = _head_size(cand_url)
        if size <= 0:
            continue
        out.append(_cand(cand_url, filename, "", "modelscope", size=size,
                         name=str(m.get("Name") or repo),
                         reference=f"{MODELSCOPE_API}/models/{repo}",
                         exact=True))
    return out


def search_models(filename: str, folder: str | None = None,
                  vram_free_gb: float | None = None,
                  offline: bool = False, limit: int = 12) -> list[dict]:
    """按文件名搜索可下载候选，附本机适配与建议落盘位置。

    源顺序：本机 Manager 目录（离线）→ hf-mirror → HuggingFace →
    Civitai → ModelScope；任一源命中即止，全部失败返回空表（调用方降级）。
    """
    want = str(filename or "").strip()
    if not want:
        return []
    results: list[dict] = []
    try:
        results = _from_manager_cache(want, folder)
    except Exception:
        results = []
    if not results and not offline:
        for src in (lambda: _from_hf(want, mirror=True),
                    lambda: _from_hf(want, mirror=False),
                    lambda: _from_civitai(want),
                    lambda: _from_modelscope(want)):
            try:
                results = src() or []
            except Exception:
                results = []
            if results:
                break

    seen: set[str] = set()
    out: list[dict] = []
    for c in results:
        key = f"{c['filename']}|{c['url']}"
        if key in seen:
            continue
        seen.add(key)
        if not c.get("folder"):
            c["folder"] = folder_hint("", c.get("type"), c["filename"]) \
                or (folder or "")
        c["fit"] = machine_fit(c["size"], vram_free_gb)
        try:
            dest = target_path(c["folder"] or folder or "", c["filename"])
            c["target_path"] = str(dest)
            c["target_dir"] = str(dest.parent)
        except PathNotAllowed as e:
            c["target_path"] = ""
            c["target_dir"] = ""
            c["folder_note"] = str(e)
        out.append(c)
    out.sort(key=lambda c: (not c["exact"], not c["fit"]["fits"], c["size"]))
    return out[:max(1, int(limit))]


# ---------------- 下载执行（安全边界 3） ----------------

_emitter = None
_finish_hook = None
_emitter_lock = threading.Lock()


def bind_emitter(fn=None, finish_hook=None) -> None:
    """接入 Web 层：fn(event, payload, project_id) 发事件；
    finish_hook(record) 在下载结束（含拒绝/失败）后回调，用于自动重跑。"""
    global _emitter, _finish_hook
    with _emitter_lock:
        if fn is not None:
            _emitter = fn
        if finish_hook is not None:
            _finish_hook = finish_hook


def _notify(event: str, rec: dict) -> None:
    payload = {k: v for k, v in rec.items() if k != "project"}
    pid = rec.get("project")
    fn = _emitter
    if fn is not None:
        try:
            fn(event, payload, pid)
            return
        except Exception:
            pass
    # 兜底：直接走事件总线（懒导入，与 runner._emit_stage 同模式）
    try:
        from brain.events import emit, project_context
        with project_context(pid):
            emit(event, payload)
    except Exception:
        pass


def _verify_file(path: Path, expect: int = 0) -> dict:
    """落位前校验：非空、大小不显著偏小、不是 HTML 错误页。"""
    try:
        size = path.stat().st_size
    except OSError:
        return {"ok": False, "reason": "文件不存在"}
    if size <= 0:
        return {"ok": False, "reason": "文件为空"}
    if expect and size < expect * 0.9:
        return {"ok": False,
                "reason": f"内容偏小（{human_size(size)} < 预期 {human_size(expect)}）"}
    try:
        with path.open("rb") as fh:
            head = fh.read(256)
    except OSError as e:
        return {"ok": False, "reason": f"读取失败：{e}"}
    low = head.lstrip()[:32].lower()
    if low.startswith(b"<!doctype") or low.startswith(b"<html"):
        return {"ok": False, "reason": "下载内容为 HTML（多为登录页或错误页）"}
    return {"ok": True, "size": size}


class DownloadManager:
    """下载登记表 + 后台执行。request() 只登记，不阻塞调用线程。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._items: dict[str, dict] = {}
        self._cancel: dict[str, threading.Event] = {}
        self._seq = 0

    # ---- 登记 ----
    def request(self, url: str, filename: str, folder: str, *,
                source: str = "", size=0, project_id: str | None = None,
                retry: dict | None = None, note: str = "",
                name: str = "", consumer: dict | None = None) -> dict:
        clean = assert_public_url(url)                  # 先校验，失败不弹窗
        dest = target_path(folder, filename)            # 路径校验同理
        # 弹窗前探测一次真实大小：大脑经常自己"估"大小甚至自己拼 URL
        # （实测把 4.71MB 的候选填成 1.2GB + 一个不存在的 HF 地址），
        # 所以以远端 Content-Length 为准，探测失败也不硬拒（有站点不支持 HEAD），
        # 但要把"未核实"如实写进弹窗。
        declared = int(size or 0)
        probed = _head_size(clean)
        warnings: list[str] = []
        if probed > 0:
            size, size_source = probed, "probe"
            if declared and not (0.8 <= declared / probed <= 1.25):
                warnings.append(
                    f"来源标注大小与实际不符（标注 {human_size(declared)}，"
                    f"实际 {human_size(probed)}），已按实际大小校验")
        else:
            size, size_source = declared, "declared"
            warnings.append("未能核实该地址的文件大小（部分站点不支持 HEAD）")
        fit = machine_fit(size)
        max_bytes = int(fit["max_gb"] * 1024 ** 3)
        if size and size > max_bytes:
            raise DownloadError(
                f"文件 {human_size(size)} 超过单文件上限 {human_size(max_bytes)}，"
                "已拒绝下载（可用 MODEL_DOWNLOAD_MAX_GB 调整上限）")
        if size and fit["disk_free"] and size + DISK_HEADROOM > fit["disk_free"]:
            raise DownloadError(
                f"磁盘空间不足：需 {human_size(size)}，"
                f"可用 {fit['disk_free_text']}")
        with self._lock:
            self._seq += 1
            did = f"dl{int(time.time() * 1000)}{self._seq:02d}"
            rec = {"id": did, "state": "awaiting_confirm", "url": clean,
                   "filename": filename, "name": name or filename,
                   "folder": folder, "source": source,
                   "size": size, "size_text": human_size(size),
                   "declared_size": declared, "size_source": size_source,
                   "warnings": warnings, "consumer": consumer or {},
                   "usable": bool((consumer or {}).get("usable", True)),
                   "received": 0, "percent": 0.0, "speed": 0.0,
                   "dest": str(dest), "target_dir": str(dest.parent),
                   "project": project_id, "retry": retry or {}, "note": note,
                   "fit": fit, "error": "", "created": time.time(),
                   "updated": time.time()}
            self._items[did] = rec
        _notify("model_download", rec)
        return dict(rec)

    # ---- 查询 ----
    def get(self, did: str) -> dict | None:
        with self._lock:
            rec = self._items.get(did)
            return dict(rec) if rec else None

    def active(self, project_id: str | None = None) -> list[dict]:
        with self._lock:
            items = [dict(r) for r in self._items.values()]
        if project_id:
            items = [r for r in items if r.get("project") == project_id]
        items.sort(key=lambda r: r.get("created", 0), reverse=True)
        return items

    # ---- 用户决定 ----
    def confirm(self, did: str) -> dict:
        with self._lock:
            rec = self._items.get(did)
            if rec is None:
                raise DownloadError(f"下载任务不存在：{did}")
            if rec["state"] != "awaiting_confirm":
                raise DownloadError(f"任务状态为 {rec['state']}，无需确认")
            rec["state"] = "downloading"
            rec["updated"] = time.time()
            rec["started"] = time.time()
        threading.Thread(target=self._run, args=(did,), daemon=True).start()
        return self.get(did) or {}

    def decline(self, did: str) -> dict:
        return self._stop(did, "declined", "用户拒绝下载")

    def cancel(self, did: str) -> dict:
        ev = self._cancel.get(did)
        if ev is not None:
            ev.set()
            return self.get(did) or {}
        return self._stop(did, "canceled", "用户取消下载")

    def _stop(self, did: str, state: str, reason: str) -> dict:
        with self._lock:
            rec = self._items.get(did)
            if rec is None:
                raise DownloadError(f"下载任务不存在：{did}")
            if rec["state"] in ("done", "failed"):
                return dict(rec)
            rec.update(state=state, error="", note=reason,
                       updated=time.time())
            done = dict(rec)
        _notify("model_download", done)
        _run_finish(done)
        return done

    # ---- 执行线程 ----
    def _run(self, did: str) -> None:
        rec = self.get(did)
        if rec is None:
            return
        dest = Path(rec["dest"])
        tmp = dest.parent / (dest.name + ".part")
        ev = threading.Event()
        self._cancel[did] = ev
        received = 0
        t0 = time.time()
        last = 0.0
        state, error, verify = "done", "", {}
        total = 0
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            with _open_stream(rec["url"], timeout=60) as resp:
                total = int(resp.headers.get("Content-Length")
                            or rec.get("size") or 0)
                with tmp.open("wb") as fh:
                    while True:
                        if ev.is_set():
                            raise Canceled("用户取消")
                        chunk = resp.read(CHUNK)
                        if not chunk:
                            break
                        fh.write(chunk)
                        received += len(chunk)
                        now = time.time()
                        if now - last >= 1.0:
                            last = now
                            self._progress(did, received, total, t0)
            if ev.is_set():
                raise Canceled("用户取消")
            # 校验基准用本次传输的 Content-Length（权威），拿不到才退回登记大小
            # ——登记大小可能是来源标注值或大脑估的
            expect = total or int(rec.get("size") or 0)
            verify = _verify_file(tmp, expect)
            if not verify.get("ok"):
                raise DownloadError(f"文件校验不通过：{verify.get('reason')}")
            os.replace(tmp, dest)
        except Canceled:
            state, error = "canceled", "用户取消"
        except (DownloadError, OSError, urllib.error.URLError, ValueError) as e:
            state, error = "failed", f"{type(e).__name__}: {e}"[:300]
        except Exception as e:                       # 兜底：绝不让线程静默死
            state, error = "failed", f"{type(e).__name__}: {e}"[:300]
        finally:
            self._cancel.pop(did, None)
            if state != "done":
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
        elapsed = max(0.001, time.time() - t0)
        with self._lock:
            cur = self._items.get(did)
            if cur is not None:
                cur.update(
                    state=state, error=error, received=received,
                    percent=100.0 if state == "done" else cur.get("percent", 0.0),
                    speed=received / elapsed, elapsed=round(elapsed, 1),
                    verify=verify, updated=time.time())
                cur["speed_text"] = human_size(int(received / elapsed)) + "/s"
                done = dict(cur)
            else:
                done = dict(rec)
        _notify("model_download", done)
        _run_finish(done)

    def _progress(self, did: str, received: int, total: int, t0: float) -> None:
        elapsed = max(0.001, time.time() - t0)
        with self._lock:
            rec = self._items.get(did)
            if rec is None:
                return
            rec.update(received=received, size=total or rec.get("size", 0),
                       size_text=human_size(total or rec.get("size", 0)),
                       percent=round(100.0 * received / total, 1) if total else 0.0,
                       speed=received / elapsed,
                       speed_text=human_size(int(received / elapsed)) + "/s",
                       elapsed=round(elapsed, 1), updated=time.time())
            snap = dict(rec)
        _notify("model_download", snap)


def _run_finish(rec: dict) -> None:
    fn = _finish_hook
    if fn is None:
        return
    try:
        fn(rec)
    except Exception:
        pass


MANAGER = DownloadManager()
