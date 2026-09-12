# -*- coding: utf-8 -*-
"""视觉通道与"本轮上传绑定"单测（离线）。

背景（实测缺陷）：用户把大脑 API 换成新 provider 后，视觉请求仍发往旧地址
（VLM_BASE_URL 是导入期常量）→ analyze_image 400；大脑没有"看不到就别编"
的规则，于是拿项目历史产物顶替刚上传的图。这里锁住四条：
  1. 视觉地址/Key 默认跟随大脑设置（env 仍最高优先）
  2. analyze_image 不传 path 就用本轮上传；路径错就报错列出候选，**不替换**
  3. 引擎：图片参数为空时自动用本轮上传；裸名精确匹配 uploads 优先于产物
  4. 视觉失败在评估结果里如实留痕（vlm_error），不再静默 pass=None
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from comfy_agent import config                     # noqa: E402

FAKE_KEY = "test-fake-key-for-vision-1234"


class _SettingsSandbox(unittest.TestCase):
    """把 AGENT_HOME 指到临时目录，避免碰真实 settings.json。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="visiontest_"))
        self._old_home = os.environ.get("AGENT_HOME")
        os.environ["AGENT_HOME"] = str(self.tmp)
        import importlib
        from comfy_agent import config as cfg
        importlib.reload(cfg)
        self.cfg = cfg
        cfg.SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        for k in ("LLM_BASE_URL", "LLM_API_KEY", "VLM_BASE_URL",
                  "VLM_API_KEY", "VLM_MODEL"):
            os.environ.pop(k, None)
        shutil.rmtree(self.tmp, ignore_errors=True)
        if self._old_home:
            os.environ["AGENT_HOME"] = self._old_home
        else:
            os.environ.pop("AGENT_HOME", None)
        import importlib
        from comfy_agent import config as cfg
        importlib.reload(cfg)

    def _write(self, **kw):
        self.cfg.save_user_settings(kw)


# ---------------- 1. 视觉地址跟随大脑 ----------------

class TestVisionResolution(_SettingsSandbox):
    def test_vision_follows_brain_settings(self):
        """只改大脑的地址/Key，视觉必须跟着走（否则发往旧 provider → 400）。"""
        from brain.llm import LLMClient, VLMClient
        self._write(llm_base_url="https://new.example/v1",
                    llm_api_key=FAKE_KEY, llm_model="brain-model",
                    vlm_model="vision-model")
        brain, vlm = LLMClient(), VLMClient()
        self.assertEqual(brain.base, "https://new.example/v1")
        self.assertEqual(vlm.base, "https://new.example/v1")   # 跟随
        self.assertEqual(vlm.key, brain.key)                   # 跟随
        self.assertEqual(vlm.model, "vision-model")            # 模型可独立

    def test_vision_dedicated_settings_win_over_brain(self):
        from brain.llm import VLMClient
        self._write(llm_base_url="https://new.example/v1",
                    llm_api_key=FAKE_KEY, llm_model="brain-model",
                    vlm_base_url="https://vlm.example/v1",
                    vlm_api_key=FAKE_KEY + "-vlm",
                    vlm_model="vision-model")
        vlm = VLMClient()
        self.assertEqual(vlm.base, "https://vlm.example/v1")
        self.assertTrue(vlm.key.endswith("-vlm"))

    def test_env_still_highest_priority(self):
        from brain.llm import VLMClient
        self._write(llm_base_url="https://new.example/v1",
                    llm_api_key=FAKE_KEY, vlm_model="vision-model")
        os.environ["VLM_BASE_URL"] = "https://env.example/v1"
        os.environ["VLM_MODEL"] = "env-vision"
        vlm = VLMClient()
        self.assertEqual(vlm.base, "https://env.example/v1")
        self.assertEqual(vlm.model, "env-vision")

    def test_effective_reports_sources_without_key(self):
        from brain.llm import VLMClient
        self._write(llm_base_url="https://new.example/v1",
                    llm_api_key=FAKE_KEY, vlm_model="vision-model")
        eff = VLMClient().effective()
        self.assertEqual(eff["base_url"], "https://new.example/v1")
        self.assertIn("跟随大脑", eff["base_source"])
        self.assertEqual(eff["key_source"], "跟随大脑")
        self.assertTrue(eff["ready"])
        # 只暴露掩码，绝不吐原 key
        self.assertNotIn(FAKE_KEY, json.dumps(eff, ensure_ascii=False))

    def test_probe_reports_unconfigured(self):
        """没有可用 key 时探针必须直接报"未配置"，不发网络请求。"""
        from unittest import mock
        from brain.llm import VLMClient
        self._write(llm_base_url="https://new.example/v1")   # 没有 key
        with mock.patch("comfy_agent.config.load_llm_api_key", return_value=""):
            res = VLMClient().probe()
        self.assertFalse(res["ok"])
        self.assertIn("Key", res["error"])


# ---------------- 2. analyze_image 与上传绑定 ----------------

class _UploadCtx:
    """最小 ToolContext 替身（不连 ComfyUI）。"""

    def __init__(self, upload_dir: Path, current_upload=None):
        self.current_upload = current_upload
        self.draft_meta = {}
        self.project_id = "p"
        self.project = type("P", (), {
            "uploads_dir": staticmethod(lambda d=upload_dir: d)})()
        self.client = None


class TestAnalyzeImageBinding(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="animg_"))
        self.up = self.tmp / "uploads"
        self.up.mkdir()
        (self.up / "new_upload.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        (self.up / "old_upload.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"1" * 32)
        self.old = self.up / "old_upload.png"
        self.new = self.up / "new_upload.png"
        # 视觉调用被替换掉：只关心"解析到了哪张图"
        from brain import tools as T
        self.T = T
        self._vlm_orig = None

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _stub_vlm(self, seen: list):
        """把 VLMClient 换成记录调用路径的假实现。"""
        import brain.llm as llm
        class FakeVLM:
            ready = True
            def analyze_image_json(self, p):
                seen.append(str(p))
                return {"content": "stub"}
            def effective(self):
                return {"base_url": "https://x", "model": "m", "ready": True}
        self._orig_cls = llm.VLMClient
        llm.VLMClient = FakeVLM
        self.addCleanup(lambda: setattr(llm, "VLMClient", self._orig_cls))

    def test_no_path_uses_current_upload(self):
        seen = []
        self._stub_vlm(seen)
        ctx = _UploadCtx(self.up, current_upload={"local_path": str(self.new),
                                                 "server_name": "new_upload.png"})
        out = self.T.tool_analyze_image(ctx, {})
        self.assertTrue(out["ok"], out)
        self.assertEqual(seen, [str(self.new)])
        self.assertEqual(out["source"], "本轮上传")

    def test_mistyped_name_does_not_substitute_another_file(self):
        """抄错名字曾按相似度命中另一张图 —— 现在必须报错并列出候选。"""
        seen = []
        self._stub_vlm(seen)
        ctx = _UploadCtx(self.up, current_upload={"local_path": str(self.new)})
        out = self.T.tool_analyze_image(ctx, {"path": "new_uplod.png"})  # 少一个 a
        self.assertFalse(out["ok"])
        self.assertEqual(seen, [])                       # 没有拿别的图去分析
        self.assertIn("new_upload.png", out["error"])     # 候选已列出

    def test_explicit_old_path_is_honored(self):
        """用户明确说旧图时仍能用（不阻断链式任务）。"""
        seen = []
        self._stub_vlm(seen)
        ctx = _UploadCtx(self.up, current_upload={"local_path": str(self.new)})
        out = self.T.tool_analyze_image(ctx, {"path": str(self.old)})
        self.assertTrue(out["ok"])
        self.assertEqual(seen, [str(self.old)])

    def test_failure_hint_forbids_substitution(self):
        from brain.llm import LLMError
        seen = []
        import brain.llm as llm
        class BoomVLM:
            ready = True
            def analyze_image_json(self, p):
                raise LLMError("LLM HTTP 400: bad model")
            def effective(self):
                return {"base_url": "https://x", "model": "m", "ready": True}
        orig = llm.VLMClient
        llm.VLMClient = BoomVLM
        self.addCleanup(lambda: setattr(llm, "VLMClient", orig))
        ctx = _UploadCtx(self.up, current_upload={"local_path": str(self.new)})
        out = self.T.tool_analyze_image(ctx, {})
        self.assertFalse(out["ok"])
        self.assertIn("400", out["error"])
        self.assertIn("编造", out["hint"])                # 明确禁止编造
        self.assertIn("禁止改用", out["hint"])             # 明确禁止替换

    def test_brain_handle_binds_current_upload(self):
        """Brain.handle 收到图片时必须写入 ctx.current_upload。"""
        from brain.agent import Brain
        import inspect
        src = inspect.getsource(Brain.handle)
        self.assertIn("current_upload", src)


# ---------------- 3. 引擎：空参数用本轮上传 + 精确匹配 ----------------

class TestEngineUploadBinding(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="engup_"))
        self.up = self.tmp / "uploads"
        self.out = self.tmp / "outputs"
        self.up.mkdir(parents=True)
        self.out.mkdir(parents=True)
        self.upload_img = self.up / "shot.png"
        self.upload_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 32)
        self.old_out = self.out / "shot.png"          # 同名旧产物（陷阱）
        self.old_out.write_bytes(b"\x89PNG\r\n\x1a\n" + b"o" * 32)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _tpl(self):
        class T:
            id = "i2i"
            input_files = [("image", "IMAGE")]
        return T()

    def _run(self, params, current_upload=None, client=None):
        from comfy_agent.runner import _ensure_inputs_uploaded
        return _ensure_inputs_uploaded(self._tpl(), dict(params), client,
                                       str(self.out), current_upload)

    def test_empty_param_filled_from_current_upload(self):
        seen = []
        class Cli:
            def upload_image(self, p):
                seen.append(str(p))
                return {"name": Path(p).name}
        params, notes, err = self._run(
            {}, current_upload={"local_path": str(self.upload_img),
                                "server_name": "shot.png"}, client=Cli())
        self.assertIsNone(err)
        self.assertEqual(params["image"], "shot.png")
        self.assertEqual(seen, [str(self.upload_img)])
        self.assertTrue(any("本轮用户上传" in n for n in notes))

    def test_bare_name_prefers_uploads_over_old_output(self):
        """裸名精确匹配：uploads/ 里的上传图优先，别再命中同名旧产物。"""
        seen = []
        class Cli:
            def upload_image(self, p):
                seen.append(str(p))
                return {"name": Path(p).name}
        params, _notes, err = self._run({"image": "shot.png"}, client=Cli())
        self.assertIsNone(err)
        self.assertEqual(seen, [str(self.upload_img)])


# ---------------- 4. 评估如实留痕 ----------------

class TestEvalVisibility(unittest.TestCase):
    def test_vlm_error_recorded_and_warned_once(self):
        import brain.eval.base as B
        from brain.eval.base import EvalResult
        r = EvalResult()
        self.assertEqual(r.vlm_error, "")
        self.assertIn("vlm_error", r.to_dict())

        events = []
        import brain.events as ev_mod
        orig = ev_mod.emit
        ev_mod.emit = lambda name, data=None: events.append((name, data))
        B._VISION_WARNED.clear()
        try:
            B._warn_vision_unavailable("LLM HTTP 400: model not found")
            B._warn_vision_unavailable("LLM HTTP 400: model not found")
        finally:
            ev_mod.emit = orig
        self.assertEqual(len(events), 1)                  # 去重：只提醒一次
        self.assertEqual(events[0][0], "stage")
        self.assertIn("视觉评估不可用", events[0][1]["detail"]["warning"])

    def test_image_eval_reports_vlm_error(self):
        """VLM 构造/调用失败 → vlm_error 有值，ok 不受影响（不误报失败）。"""
        import brain.eval.base as B
        import brain.llm as llm
        orig = llm.VLMClient
        class Boom:
            def __init__(self):
                raise RuntimeError("boom")
        llm.VLMClient = Boom
        try:
            png = self._png()
            res = B.evaluate([str(png)], "criteria", sample=1)
        finally:
            llm.VLMClient = orig
        self.assertTrue(res.vlm_error)
        self.assertIn("boom", res.vlm_error)

    @staticmethod
    def _png():
        import tempfile
        d = Path(tempfile.mkdtemp(prefix="evalpng_"))
        p = d / "a.png"
        # 8x8 PNG（最小可用尺寸：Tier0 需要能读出宽高）
        p.write_bytes(bytes.fromhex(
            "89504e470d0a1a0a0000000d4948445200000008000000080806000000"
            "1f15c4890000000a49444154789c6360000002000100ffff03000006"
            "0005574bd3a20000000049454e44ae426082"))
        return p


if __name__ == "__main__":
    unittest.main(verbosity=2)
