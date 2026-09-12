# -*- coding: utf-8 -*-
"""Web UI 支撑层单测：事件总线 / 会话适配器 / 流式解析 / 路径防穿越。"""
import json
import queue
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class TestEventBus(unittest.TestCase):
    def test_emit_and_subscribe(self):
        from brain.events import EventBus
        bus = EventBus()
        got = []
        bus.subscribe(lambda m: got.append(m))
        bus.emit("stage", {"stage": "running"})
        self.assertEqual(got[0]["event"], "stage")
        self.assertEqual(got[0]["data"]["stage"], "running")
        self.assertIn("ts", got[0])

    def test_bad_subscriber_does_not_break(self):
        from brain.events import EventBus
        bus = EventBus()
        bus.subscribe(lambda m: (_ for _ in ()).throw(Exception("boom")))
        got = []
        bus.subscribe(lambda m: got.append(m))
        bus.emit("x", {})
        self.assertEqual(len(got), 1)   # 坏订阅者不影响好订阅者

    def test_session_adapter_and_replay(self):
        from brain.events import SessionAdapter, replay
        p = Path(__file__).parent / "_session_test.jsonl"
        p.unlink(missing_ok=True)
        try:
            a = SessionAdapter(p)
            a.on_event({"event": "user_message", "data": {"text": "画猫"},
                        "ts": 1.0})
            events = replay(p)
            self.assertEqual(events[0]["event"], "user_message")
            self.assertEqual(events[0]["data"]["text"], "画猫")
        finally:
            p.unlink(missing_ok=True)


class TestChatStream(unittest.TestCase):
    def _fake_sse(self):
        """伪造 OpenAI 流式响应体。"""
        chunks = [
            'data: {"choices":[{"delta":{"reasoning_content":"想"}}]}\n\n',
            'data: {"choices":[{"delta":{"reasoning_content":"一下"}}]}\n\n',
            'data: {"choices":[{"delta":{"content":"OK"}}]}\n\n',
            'data: [DONE]\n\n',
        ]
        return [c.encode("utf-8") for c in chunks]

    def test_stream_parses_both_channels(self):
        from brain.llm import LLMClient
        llm = LLMClient.__new__(LLMClient)   # 跳过 __init__（不读key）
        llm.base = "https://api.example.com"
        llm.key = "k"
        llm.model = "m"
        fake_resp = mock.Mock()
        fake_resp.__enter__ = mock.Mock(return_value=fake_resp)
        fake_resp.__exit__ = mock.Mock(return_value=False)
        fake_resp.__iter__ = mock.Mock(return_value=iter(self._fake_sse()))
        opener = mock.Mock()
        opener.open.return_value = fake_resp
        llm._opener = opener
        with mock.patch("brain.llm._assert_safe_url"):
            out = list(llm.chat_stream([{"role": "user", "content": "x"}]))
        self.assertEqual(out, [("reasoning", "想"), ("reasoning", "一下"),
                               ("content", "OK")])

    def test_stream_rejected_falls_back(self):
        import urllib.error
        from brain.llm import LLMClient, LLMError
        llm = LLMClient.__new__(LLMClient)
        llm.base = "https://api.example.com"
        llm.key = "k"
        llm.model = "m"
        opener = mock.Mock()
        opener.open.side_effect = urllib.error.HTTPError(
            "u", 400, "bad", {}, None)
        llm._opener = opener
        with mock.patch("brain.llm._assert_safe_url"), \
             mock.patch.object(llm, "chat", return_value="回退文本"):
            out = list(llm.chat_stream([{"role": "user", "content": "x"}]))
        self.assertEqual(out, [("content", "回退文本")])


class TestOutputPathSafety(unittest.TestCase):
    def test_traversal_rejected(self):
        from brain.web.server import safe_output_path
        self.assertIsNone(safe_output_path("../../windows/system32/x.png"))
        self.assertIsNone(safe_output_path("..%2F..%2Fetc.png"))

    def test_legit_path_in_dir(self):
        from brain.web.server import safe_output_path
        from brain.projects import ProjectStore
        import shutil
        p = ProjectStore().create("安全测试项目")
        f = p.outputs_dir() / "_safety_test.png"
        f.write_bytes(b"x")
        try:
            rel = f"{p.id}/outputs/_safety_test.png"
            got = safe_output_path(rel)
            self.assertIsNotNone(got)
            self.assertEqual(got.name, "_safety_test.png")
        finally:
            shutil.rmtree(p.dir, ignore_errors=True)

    def test_upload_file_served_via_outputs(self):
        from brain.web.server import safe_output_path
        from brain.projects import ProjectStore
        import shutil
        p = ProjectStore().create("上传服务测试")
        f = p.uploads_dir() / "up_test.png"
        f.write_bytes(b"x")
        try:
            got = safe_output_path(f"{p.id}/uploads/up_test.png")
            self.assertIsNotNone(got)
            self.assertEqual(got.name, "up_test.png")
        finally:
            shutil.rmtree(p.dir, ignore_errors=True)


class TestUploadValidation(unittest.TestCase):
    def test_png_jpeg_webp_accepted(self):
        from brain.web.server import validate_image_bytes
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
        jpg = b"\xff\xd8\xff\xe0" + b"\x00" * 16
        webp = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 16
        for raw in (png, jpg, webp):
            ok, reason = validate_image_bytes(raw)
            self.assertTrue(ok, reason)

    def test_garbage_rejected(self):
        from brain.web.server import validate_image_bytes
        ok, _ = validate_image_bytes(b"hello world, not an image")
        self.assertFalse(ok)
        ok, _ = validate_image_bytes(b"")
        self.assertFalse(ok)
        # RIFF 但不是 WEBP
        ok, _ = validate_image_bytes(b"RIFF\x00\x00\x00\x00AVI ")
        self.assertFalse(ok)

    def test_oversize_rejected(self):
        from brain.web.server import validate_image_bytes
        with mock.patch("brain.web.server.MAX_UPLOAD_BYTES", 10):
            ok, _ = validate_image_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
            self.assertFalse(ok)

    def test_safe_upload_name(self):
        from brain.web.server import safe_upload_name
        self.assertEqual(safe_upload_name("猫娘.png"), "__.png")
        self.assertEqual(safe_upload_name("a b c.jpg"), "a_b_c.jpg")
        self.assertEqual(safe_upload_name("noext"), "noext.png")
        self.assertEqual(safe_upload_name("evil.exe"), "evil.png")
        self.assertTrue(safe_upload_name("ok_1.PNG").endswith(".png"))


class TestDownloadAutoRerun(unittest.TestCase):
    """下载结束 → 大脑收件箱投递：成功自动重跑，拒绝/失败走降级。

    用假的 SESSION 验证消息内容与"同一任务只自动重跑一次"的护栏，
    不启动真服务器、不发事件。
    """

    def setUp(self):
        from brain.web import server
        self.server = server
        self._old = server.SESSION
        server._AUTO_RETRY.clear()

        class _BS:
            def __init__(self):
                self.inbox = queue.Queue()

        class _Sess:
            default_id = "project"

            def __init__(self):
                self.bs = _BS()

            def session_for(self, pid=None):
                return self.bs

        self.sess = _Sess()
        server.SESSION = self.sess

    def tearDown(self):
        self.server.SESSION = self._old
        self.server._AUTO_RETRY.clear()

    def _rec(self, **kw):
        base = {"state": "done", "filename": "m.safetensors",
                "size_text": "4.71 MB", "dest": r"C:\models\vae_approx\m.safetensors",
                "project": "project", "error": "",
                "retry": {"template": "t2i", "params": {"prompt": "猫"}}}
        base.update(kw)
        return base

    def test_success_enqueues_rerun_with_template(self):
        self.server._download_finished(self._rec())
        text = self.sess.bs.inbox.get_nowait()["text"]
        self.assertIn("已下载完成", text)
        self.assertIn("run_template", text)
        self.assertIn("t2i", text)

    def test_success_retries_only_once(self):
        self.server._download_finished(self._rec())
        self.sess.bs.inbox.get_nowait()
        self.server._download_finished(self._rec())
        text = self.sess.bs.inbox.get_nowait()["text"]
        self.assertIn("已自动重跑过一次", text)
        self.assertNotIn("run_template", text)

    def test_declined_enqueues_degrade(self):
        self.server._download_finished(self._rec(state="declined"))
        text = self.sess.bs.inbox.get_nowait()["text"]
        self.assertIn("拒绝", text)
        self.assertIn("降级", text)

    def test_failed_enqueues_reason(self):
        self.server._download_finished(
            self._rec(state="failed", error="HTTPError: 404"))
        text = self.sess.bs.inbox.get_nowait()["text"]
        self.assertIn("404", text)
        self.assertIn("降级", text)

    def test_canceled_does_not_message_brain(self):
        self.server._download_finished(self._rec(state="canceled"))
        self.assertTrue(self.sess.bs.inbox.empty())


if __name__ == "__main__":
    unittest.main(verbosity=2)
