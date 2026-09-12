# -*- coding: utf-8 -*-
"""设置与可移植性单测：settings 优先级/脱敏/落盘、路径解析、ffmpeg 查找链。"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 测试用假占位符（非真实凭据，仅验证存取逻辑）
FAKE_KEY_A = "test-fake-key-not-a-secret"
FAKE_KEY_B = "test-another-fake-key-value"


class TestSettingsStore(unittest.TestCase):
    def setUp(self):
        from comfy_agent import config as cfg
        self._old_home = os.environ.get("AGENT_HOME")
        os.environ["AGENT_HOME"] = str(Path(__file__).parent / "_settings_tmp")
        import importlib
        importlib.reload(cfg)
        self.cfg = cfg
        cfg.SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.cfg.SETTINGS_PATH.parent, ignore_errors=True)
        if self._old_home:
            os.environ["AGENT_HOME"] = self._old_home
        else:
            os.environ.pop("AGENT_HOME", None)

    def test_save_load_roundtrip(self):
        self.cfg.save_user_settings({"llm_api_key": FAKE_KEY_A,
                                     "llm_model": "glm-x"})
        s = self.cfg.load_user_settings()
        self.assertEqual(s["llm_api_key"], FAKE_KEY_A)

    def test_settings_file_in_temp_home(self):
        assert str(self.cfg.SETTINGS_PATH).startswith(
            str(Path(__file__).parent / "_settings_tmp")), "落盘位置错误"

    def test_key_priority_settings_over_registry(self):
        """settings.json 有 key 时注册表不再被查（隔离链）。"""
        self.cfg.save_user_settings({"llm_api_key": FAKE_KEY_B})
        self.cfg.LLM_API_KEY = ""
        self.cfg.VLM_API_KEY = ""
        got = self.cfg.load_llm_api_key()
        self.assertEqual(got, FAKE_KEY_B)

    def test_masked_output(self):
        from brain.llm import LLMClient
        self.cfg.save_user_settings({"llm_api_key": FAKE_KEY_A,
                                     "llm_base_url":
                                     "https://api.example.com"})
        c = LLMClient()
        m = c.masked()
        self.assertNotIn(FAKE_KEY_A, m["api_key_masked"])
        self.assertTrue(m["api_key_masked"].startswith("test"))
        self.assertTrue(m["api_key_masked"].endswith("cret"))


class TestPortability(unittest.TestCase):
    def test_agent_home_derived_not_hardcoded(self):
        """AGENT_HOME 默认值由 __file__ 推导（源码无硬编码个人路径）。"""
        from comfy_agent import config as cfg
        if "AGENT_HOME" in os.environ:
            self.skipTest("环境变量存在，跳过默认值断言")
        # 默认值 = 项目根/.comfy-agent（推导），且源码默认值不含拼写出的绝对路径
        self.assertEqual(cfg.AGENT_HOME, cfg.PROJECT_ROOT / ".comfy-agent")
        src = (ROOT / "comfy_agent" / "config.py").read_text(encoding="utf-8")
        self.assertNotIn('"C:\\\\Users', src)
        self.assertNotIn("'C:\\\\Users", src)

    def test_project_root_relative(self):
        from comfy_agent import config as cfg
        if "AGENT_HOME" not in os.environ:
            self.assertTrue(str(cfg.AGENT_HOME).startswith(str(cfg.PROJECT_ROOT)))

    def test_ffmpeg_finder_env_priority(self):
        import brain.eval.video as v
        from importlib import reload
        with mock.patch.dict(os.environ,
                             {"FFMPEG_PATH": "C:/my/ffmpeg.exe"}):
            reload(v)
            self.assertEqual(v.FFMPEG, "C:/my/ffmpeg.exe")
        reload(v)   # 恢复

    def test_comfy_root_accepts_portable_root(self):
        """comfy_root.local/一键启动.bat 存的是整合包根目录（python\\python.exe
        与 ComfyUI\\main.py 在那里）；配置必须归一为内层 ComfyUI 目录，否则
        MODELS_DIR/MANAGER_CACHE 指向不存在的路径、缓存增强静默失效。"""
        import tempfile
        from comfy_agent.config import _normalize_comfy_root
        with tempfile.TemporaryDirectory() as tmp:
            portable = Path(tmp) / "ComfyUI-aki-v3"
            inner = portable / "ComfyUI"
            (inner / "models").mkdir(parents=True)
            (inner / "main.py").write_text("", encoding="utf-8")
            (portable / "python").mkdir()
            self.assertEqual(_normalize_comfy_root(str(portable)), inner)
            self.assertEqual(_normalize_comfy_root(str(inner)), inner)
            # 两种形态都不像 ComfyUI 时原样返回（不猜、不报错）
            other = Path(tmp) / "not-comfy"
            other.mkdir()
            self.assertEqual(_normalize_comfy_root(str(other)), other)


if __name__ == "__main__":
    unittest.main(verbosity=2)
