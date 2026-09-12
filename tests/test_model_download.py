# -*- coding: utf-8 -*-
"""缺模型搜索/下载单测（全部离线，不联网）。

覆盖三道安全边界与状态机：
  URL 白名单（回环/私有/保留/凭据/协议 + 重定向逐跳复校验）
  路径安全（穿越/盘符/绝对路径/后缀/未知目录）
  下载状态机（awaiting_confirm → downloading → done/failed/declined/canceled）
以及大小解析与本机 Manager 目录候选解析（依赖本机缓存，缺失则跳过）。
"""
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from comfy_agent import config                     # noqa: E402
from comfy_agent import model_download as md       # noqa: E402


def _resolver(ip):
    """假 DNS：把任意 host 解析为指定 IP（避免测试联网）。"""
    return lambda host, port: [ip]


PUBLIC_IP = "93.184.216.34"


class _FakeResp:
    """假 HTTP 响应：按块吐出预置字节（供下载线程消费）。"""

    def __init__(self, chunks, length=None):
        self._chunks = list(chunks)
        total = length if length is not None else sum(len(c) for c in chunks)
        self.headers = {"Content-Length": str(total)}

    def read(self, n=-1):
        return self._chunks.pop(0) if self._chunks else b""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mdltest_"))
        self._old_root = config.COMFY_ROOT
        self._old_models = config.MODELS_DIR
        config.COMFY_ROOT = self.tmp
        config.MODELS_DIR = self.tmp / "models"
        (config.MODELS_DIR / "checkpoints").mkdir(parents=True)
        self._old_open = md._open_stream
        self._old_head = md._head_size
        self._old_emitter = md._emitter
        self._old_hook = md._finish_hook
        md.bind_emitter(lambda *a: None)       # 测试不发事件、不导入 brain
        # 让任何"探测远端大小"的兜底路径都不出网（实测 size=0 时会 HEAD 真站）
        md._head_size = lambda url, timeout=20: 0

    def tearDown(self):
        md._open_stream = self._old_open
        md._head_size = self._old_head
        md._emitter = self._old_emitter
        md._finish_hook = self._old_hook
        config.COMFY_ROOT = self._old_root
        config.MODELS_DIR = self._old_models
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def _wait(mgr, did, want=("done", "failed", "canceled", "declined"),
              timeout=5.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            rec = mgr.get(did) or {}
            if rec.get("state") in want:
                return rec
            time.sleep(0.02)
        return mgr.get(did) or {}


# ---------------- 安全边界 1：出网 ----------------

class TestUrlGuard(unittest.TestCase):
    def test_rejects_non_public_addresses(self):
        for ip in ("127.0.0.1", "10.0.0.5", "192.168.1.20", "172.16.3.4",
                   "169.254.1.1", "0.0.0.0", "224.0.0.1", "240.0.0.1",
                   "100.64.0.1", "::1", "fe80::1"):
            with self.assertRaises(md.UrlNotAllowed, msg=ip):
                md.assert_public_url("http://evil.test/model.safetensors",
                                     resolver=_resolver(ip))

    def test_rejects_bad_scheme_and_credentials(self):
        for url in ("file:///c:/windows/win.ini", "ftp://host/x.pt",
                    "http://user:pw@host/x.pt", "http:///x.pt", "notaurl"):
            with self.assertRaises(md.UrlNotAllowed, msg=url):
                md.assert_public_url(url, resolver=_resolver(PUBLIC_IP))

    def test_accepts_public_http_and_https(self):
        for url in ("https://huggingface.co/a/b.safetensors",
                    "http://93.184.216.34/x.pt"):
            self.assertEqual(
                md.assert_public_url(url, resolver=_resolver(PUBLIC_IP)), url)

    def test_resolution_failure_is_rejected(self):
        def boom(host, port):
            raise OSError("dns down")
        with self.assertRaises(md.UrlNotAllowed):
            md.assert_public_url("https://nope.test/x.pt", resolver=boom)

    def test_redirect_revalidated_per_hop(self):
        """公网地址 302 到内网必须被拦（否则等于绕过 SSRF 防护）。"""
        import urllib.request
        h = md._ValidatingRedirect()
        req = urllib.request.Request("https://public.test/a.safetensors")
        with self.assertRaises(md.UrlNotAllowed):
            h.redirect_request(req, None, 302, "Found", {},
                               "http://10.1.2.3/a.safetensors")
        with self.assertRaises(md.UrlNotAllowed):
            h.redirect_request(req, None, 302, "Found", {},
                               "http://127.0.0.1:8188/a.safetensors")


# ---------------- 安全边界 2：落盘路径 ----------------

class TestPathGuard(_Base):
    def test_traversal_folder_rejected(self):
        for folder in ("..", ".", "a/b", "", "C:", "checkpoints/x", "/tmp",
                       "con"):
            with self.assertRaises(md.PathNotAllowed, msg=folder):
                md.target_path(folder, "x.safetensors")

    def test_traversal_filename_rejected(self):
        for name in ("../x.safetensors", "a/../../x.safetensors",
                     "/abs/x.safetensors", "C:/x.safetensors",
                     "sub/../../../x.safetensors", "x.exe", "x.safetensors.exe",
                     "..", "sub/.hidden.safetensors", "nul.safetensors"):
            with self.assertRaises(md.PathNotAllowed, msg=name):
                md.target_path("checkpoints", name)

    def test_unknown_folder_rejected_unless_present(self):
        with self.assertRaises(md.PathNotAllowed):
            md.target_path("random_dir", "x.safetensors")
        (config.MODELS_DIR / "random_dir").mkdir()
        self.assertTrue(str(md.target_path("random_dir", "x.safetensors"))
                        .endswith("random_dir\\x.safetensors")
                        or str(md.target_path("random_dir", "x.safetensors"))
                        .endswith("random_dir/x.safetensors"))

    def test_valid_target_stays_inside_models(self):
        dest = md.target_path("checkpoints", "sub/x.safetensors")
        root = config.MODELS_DIR.resolve()
        self.assertTrue(str(dest).startswith(str(root)))
        self.assertIn(root, dest.parents)

    def test_unconfigured_root_rejected(self):
        config.COMFY_ROOT = ""
        with self.assertRaises(md.PathNotAllowed):
            md.target_path("checkpoints", "x.safetensors")


# ---------------- 大小与目录推断 ----------------

class TestSizeAndHint(unittest.TestCase):
    def test_parse_size(self):
        self.assertEqual(md.parse_size("3.53GB"), int(3.53 * 1024 ** 3))
        self.assertEqual(md.parse_size("4.71MB"), int(4.71 * 1024 ** 2))
        self.assertEqual(md.parse_size("512"), 512)
        self.assertEqual(md.parse_size(2048), 2048)
        self.assertEqual(md.parse_size("bogus"), 0)
        self.assertEqual(md.parse_size("1.5XB"), 0)
        self.assertEqual(md.parse_size(None), 0)

    def test_human_size(self):
        self.assertEqual(md.human_size(0), "未知")
        self.assertEqual(md.human_size(1024 ** 3), "1.00 GB")
        self.assertIn("MB", md.human_size(5 * 1024 ** 2))

    def test_folder_hint(self):
        self.assertEqual(md.folder_hint("checkpoints/LTXV", "checkpoint"), "checkpoints")
        self.assertEqual(md.folder_hint("", "lora"), "loras")
        self.assertEqual(md.folder_hint("", "", "a.safetensors"), "checkpoints")
        self.assertEqual(md.folder_hint("weird", "", "a.mp4"), "")


# ---------------- 搜索（本机 Manager 目录） ----------------

class TestSearch(_Base):
    def test_unknown_filename_returns_empty_offline(self):
        self.assertEqual(
            md.search_models("definitely-not-a-real-model-xyz.safetensors",
                             offline=True), [])

    def test_blank_query_returns_empty(self):
        self.assertEqual(md.search_models("", offline=True), [])
        self.assertEqual(md.search_models("   ", offline=True), [])

    def test_manager_catalog_hit(self):
        cache = Path(config.MANAGER_CACHE)
        if not cache.is_dir() or not list(cache.glob("*model-list.json")):
            self.skipTest("本机无 ComfyUI-Manager 模型目录")
        want = "ltx-video-2b-v0.9.1.safetensors"
        cands = md.search_models(want, offline=True)
        if not cands:
            self.skipTest("Manager 目录里没有该文件")
        top = cands[0]
        self.assertEqual(top["filename"], want)          # 同名候选排最前
        self.assertEqual(top["folder"], "checkpoints")
        self.assertGreater(top["size"], 1024 ** 3)
        self.assertEqual(top["source"], "manager")
        self.assertTrue(top["fit"]["fits"])
        self.assertTrue(top["target_dir"])

    def test_folder_filter_applied(self):
        cands = md.search_models("ltx-video-2b-v0.9.1.safetensors",
                                 folder="loras", offline=True)
        for c in cands:
            self.assertEqual(c["folder"], "loras")


# ---------------- 安全边界 3：下载状态机 ----------------

class TestDownloadManager(_Base):
    FAKE = b"0" * 4096 + b"PK"          # 非 HTML 的哑数据

    def _request(self, mgr, **kw):
        kw.setdefault("url", "https://huggingface.co/a/b.safetensors")
        kw.setdefault("filename", "b.safetensors")
        kw.setdefault("folder", "checkpoints")
        kw.setdefault("size", len(self.FAKE))
        return mgr.request(**kw)

    def test_request_rejects_private_url(self):
        mgr = md.DownloadManager()
        with self.assertRaises(md.UrlNotAllowed):
            self._request(mgr, url="http://127.0.0.1:8188/steal.safetensors")

    def test_request_rejects_oversize(self):
        mgr = md.DownloadManager()
        old = os.environ.get("MODEL_DOWNLOAD_MAX_GB")
        os.environ["MODEL_DOWNLOAD_MAX_GB"] = "1"
        try:
            with self.assertRaises(md.DownloadError):
                self._request(mgr, size=2 * 1024 ** 3)
        finally:
            if old is None:
                os.environ.pop("MODEL_DOWNLOAD_MAX_GB", None)
            else:
                os.environ["MODEL_DOWNLOAD_MAX_GB"] = old

    def test_decline_and_cancel_states(self):
        mgr = md.DownloadManager()
        a = self._request(mgr)
        self.assertEqual(a["state"], "awaiting_confirm")
        self.assertEqual(mgr.decline(a["id"])["state"], "declined")
        b = self._request(mgr)
        self.assertEqual(mgr.cancel(b["id"])["state"], "canceled")
        self.assertEqual(len(mgr.active()), 2)
        self.assertEqual(len(mgr.active("other-project")), 0)
        with self.assertRaises(md.DownloadError):
            mgr.confirm(a["id"])                 # 已拒绝，不能再确认

    def test_confirm_requires_existing_id(self):
        mgr = md.DownloadManager()
        with self.assertRaises(md.DownloadError):
            mgr.confirm("nope")

    def test_download_writes_file_and_cleans_part(self):
        mgr = md.DownloadManager()
        md._open_stream = lambda url, timeout=30, headers=None: \
            _FakeResp([self.FAKE[:2048], self.FAKE[2048:]])
        rec = self._request(mgr)
        rec = mgr.confirm(rec["id"])
        self.assertEqual(rec["state"], "downloading")
        done = self._wait(mgr, rec["id"])
        self.assertEqual(done["state"], "done", done.get("error"))
        dest = Path(done["dest"])
        self.assertEqual(dest.read_bytes(), self.FAKE)
        self.assertFalse((dest.parent / (dest.name + ".part")).exists())
        self.assertGreater(done["percent"], 99)
        self.assertTrue(done["verify"]["ok"])

    def test_failed_download_leaves_no_file(self):
        mgr = md.DownloadManager()
        html = b"<!DOCTYPE html><html>login required</html>"
        md._open_stream = lambda url, timeout=30, headers=None: \
            _FakeResp([html])
        rec = self._request(mgr, size=len(html))
        rec = mgr.confirm(rec["id"])
        done = self._wait(mgr, rec["id"])
        self.assertEqual(done["state"], "failed")
        self.assertIn("HTML", done["error"])
        self.assertFalse(Path(done["dest"]).exists())

    def test_truncated_download_rejected(self):
        """内容显著小于声明大小 → 判失败，绝不把半截文件放进 models。"""
        mgr = md.DownloadManager()
        md._open_stream = lambda url, timeout=30, headers=None: \
            _FakeResp([b"0" * 1024], length=8 * 1024 ** 2)
        rec = mgr.confirm(self._request(mgr, size=8 * 1024 ** 2)["id"])
        done = self._wait(mgr, rec["id"])
        self.assertEqual(done["state"], "failed")
        self.assertIn("偏小", done["error"])
        self.assertFalse(Path(done["dest"]).exists())

    def test_http_error_marks_failed(self):
        mgr = md.DownloadManager()

        def boom(url, timeout=30, headers=None):
            raise OSError("connection reset")
        md._open_stream = boom
        rec = mgr.confirm(self._request(mgr)["id"])
        done = self._wait(mgr, rec["id"])
        self.assertEqual(done["state"], "failed")
        self.assertIn("connection reset", done["error"])


class TestRequestProbe(_Base):
    """弹窗前的大小核实：大脑常自估大小/自拼 URL，弹窗必须显示真实信息。"""

    # 字面公网 IP：DNS 解析本地即可完成（测试不出网）
    URL = "http://93.184.216.34/a.safetensors"

    def test_probed_size_overrides_declared(self):
        md._head_size = lambda url, timeout=20: 4943336
        mgr = md.DownloadManager()
        rec = mgr.request(self.URL, "a.safetensors",
                          "checkpoints", size=md.parse_size("1.2 GB"))
        self.assertEqual(rec["size"], 4943336)
        self.assertEqual(rec["size_source"], "probe")
        self.assertTrue(any("不符" in w for w in rec["warnings"]))

    def test_probe_failure_is_disclosed_not_rejected(self):
        md._head_size = lambda url, timeout=20: 0
        mgr = md.DownloadManager()
        rec = mgr.request(self.URL, "a.safetensors",
                          "checkpoints", size=1024)
        self.assertEqual(rec["size"], 1024)
        self.assertEqual(rec["size_source"], "declared")
        self.assertTrue(any("未能核实" in w for w in rec["warnings"]))

    def test_confirm_uses_response_length_for_verify(self):
        """登记大小错得离谱也不该误杀：校验基准是本次传输的 Content-Length。"""
        mgr = md.DownloadManager()
        md._head_size = lambda url, timeout=20: 0
        payload = b"0" * 4096
        md._open_stream = lambda url, timeout=30, headers=None: \
            _FakeResp([payload])            # Content-Length = 4096
        rec = mgr.request(self.URL, "a.safetensors",
                          "checkpoints", size=10 * 1024 ** 2)   # 声明 10MB
        done = self._wait(mgr, mgr.confirm(rec["id"])["id"])
        self.assertEqual(done["state"], "done", done.get("error"))
        self.assertEqual(Path(done["dest"]).read_bytes(), payload)


class _StubCtx:
    """download_model 需要的最小上下文（不连 ComfyUI）。"""

    def __init__(self):
        self.draft_meta = {}
        self.project_id = "proj_stub"
        self.project = None
        self.client = None


class TestToolUrlCorrection(_Base):
    """大脑自拼 URL/自估大小时，工具用 search_models 的权威候选校正。"""

    CAND_URL = "http://93.184.216.34/catalog/taef1_decoder.pth"
    HALLUCINATED = ("https://huggingface.co/taesd/taesd/resolve/main/"
                    "taef1_decoder.pth")

    def _ctx_with_candidate(self):
        ctx = _StubCtx()
        ctx.draft_meta["_model_candidates"] = {
            "taef1_decoder.pth": [{
                "filename": "taef1_decoder.pth", "folder": "vae_approx",
                "source": "manager", "size": 4943336, "size_text": "4.71 MB",
                "url": self.CAND_URL}]}
        return ctx

    def test_hallucinated_url_replaced_by_candidate(self):
        from brain.tools import tool_download_model
        md._head_size = lambda url, timeout=20: 4943336
        ctx = self._ctx_with_candidate()
        out = tool_download_model(ctx, {
            "url": self.HALLUCINATED,
            "filename": "taef1_decoder.pth", "folder": "vae_approx",
            "size": "1.2 GB", "source": "Hugging Face"})
        self.assertTrue(out["ok"], out)
        self.assertTrue(out.get("url_corrected"))
        rec = md.MANAGER.get(out["download_id"])
        self.assertEqual(rec["url"], self.CAND_URL)
        self.assertEqual(rec["size"], 4943336)      # 用候选大小，不用"1.2 GB"
        self.assertEqual(rec["source"], "manager")
        md.MANAGER.decline(rec["id"])

    def test_missing_args_rejected(self):
        from brain.tools import tool_download_model
        out = tool_download_model(_StubCtx(), {"filename": "x.safetensors"})
        self.assertFalse(out["ok"])
        self.assertIn("url", out["error"])

    def test_stateful_search_caches_candidates(self):
        """search_models 的结果要留在会话里，供 download_model 校正。"""
        if not Path(config.MANAGER_CACHE).is_dir():
            self.skipTest("本机无 ComfyUI-Manager 模型目录")
        from brain.tools import tool_search_models
        ctx = _StubCtx()
        out = tool_search_models(ctx, {"filename": "taef1_decoder.pth",
                                       "offline": True})
        if not out.get("count"):
            self.skipTest("Manager 目录无该文件")
        cached = ctx.draft_meta["_model_candidates"]["taef1_decoder.pth"]
        self.assertTrue(cached[0]["url"].startswith("http"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
