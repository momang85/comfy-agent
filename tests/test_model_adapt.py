# -*- coding: utf-8 -*-
"""跨设备模型适配单测：自动选模型/同家族替换/model_prefs 优先级/引擎集成。

模拟他人设备：模型清单与模板默认模型（novaAnimeXL/anything-v5）不一致时，
图像模板应自动绑定本机 checkpoint，而不是 validation_failed。
"""
import copy
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.test_synth import SNAPSHOT  # noqa: E402


def _k(checkpoints, snapshot=None):
    from comfy_agent.knowledge import Knowledge
    return Knowledge(snapshot or SNAPSHOT, {},
                     {"checkpoints": list(checkpoints)})


def _patched_settings(settings):
    return mock.patch("comfy_agent.config.load_user_settings",
                      return_value=settings)


class TestPickLocalCheckpoint(unittest.TestCase):
    def test_empty_list_returns_none(self):
        from comfy_agent.model_adapt import pick_local_checkpoint
        self.assertIsNone(pick_local_checkpoint("sdxl", _k([])))

    def test_prefs_priority(self):
        from comfy_agent.model_adapt import pick_local_checkpoint
        cps = ["anythingXL_v5.safetensors", "my_fav_xl.safetensors"]
        got = pick_local_checkpoint("sdxl", _k(cps),
                                    prefs={"sdxl": "my_fav_xl.safetensors"})
        self.assertEqual(got, "my_fav_xl.safetensors")

    def test_same_family_without_prefs(self):
        from comfy_agent.model_adapt import pick_local_checkpoint
        cps = ["majicmixRealistic_v7.safetensors",
               "juggernautXL_v9.safetensors"]
        got = pick_local_checkpoint("sdxl", _k(cps))
        self.assertEqual(got, "juggernautXL_v9.safetensors")

    def test_other_family_pref_fallback(self):
        """本机没有 SDXL 模型时，退到另一家族的偏好模型。"""
        from comfy_agent.model_adapt import pick_local_checkpoint
        cps = ["dreamshaper_8.safetensors", "my_fav_sd15.safetensors"]
        got = pick_local_checkpoint("sdxl", _k(cps),
                                    prefs={"sd15": "my_fav_sd15.safetensors"})
        self.assertEqual(got, "my_fav_sd15.safetensors")

    def test_any_checkpoint_fallback(self):
        from comfy_agent.model_adapt import pick_local_checkpoint
        got = pick_local_checkpoint("sdxl", _k(["dreamshaper_8.safetensors"]))
        self.assertEqual(got, "dreamshaper_8.safetensors")


class TestAdaptCkpt(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from comfy_agent.templates.image import (T2I, SDXL_CKPT,
                                                 SD15_CKPT, I2I)
        from comfy_agent.templates.video import MiniMaxT2V
        cls.T2I, cls.SDXL, cls.SD15, cls.I2I = T2I, SDXL_CKPT, SD15_CKPT, I2I
        cls.video = MiniMaxT2V()

    def test_default_present_unchanged(self):
        from comfy_agent.model_adapt import adapt_ckpt
        k = _k([self.SDXL])
        params, notes = adapt_ckpt(self.T2I(self.SDXL), {}, k)
        self.assertEqual(params, {})
        self.assertEqual(notes, [])

    def test_default_missing_picks_same_family(self):
        from comfy_agent.model_adapt import adapt_ckpt
        k = _k(["juggernautXL_v9.safetensors"])
        params, notes = adapt_ckpt(self.T2I(self.SDXL), {}, k)
        self.assertEqual(params["ckpt"], "juggernautXL_v9.safetensors")
        self.assertTrue(notes)
        self.assertNotIn("width", params)   # SDXL 家族不变，分辨率不动

    def test_default_missing_sd15_only_clamps_resolution(self):
        """本机只有 SD1.5 模型：兜底替换并把默认分辨率收敛到 512。"""
        from comfy_agent.model_adapt import adapt_ckpt
        k = _k(["dreamshaper_8.safetensors"])
        params, notes = adapt_ckpt(self.T2I(self.SDXL), {}, k)
        self.assertEqual(params["ckpt"], "dreamshaper_8.safetensors")
        self.assertEqual(params["width"], 512)
        self.assertEqual(params["height"], 512)

    def test_explicit_missing_same_family_replaced(self):
        from comfy_agent.model_adapt import adapt_ckpt
        k = _k(["dreamshaper_8.safetensors"])
        params, notes = adapt_ckpt(self.T2I(self.SDXL),
                                   {"ckpt": self.SD15}, k)
        self.assertEqual(params["ckpt"], "dreamshaper_8.safetensors")
        self.assertTrue(notes)

    def test_explicit_missing_cross_family_not_replaced(self):
        """显式指定的 SDXL 模型缺失、本机只有 SD1.5：不跨家族替换，报缺。"""
        from comfy_agent.model_adapt import adapt_ckpt
        k = _k(["dreamshaper_8.safetensors"])
        params, notes = adapt_ckpt(self.T2I(self.SDXL),
                                   {"ckpt": self.SDXL}, k)
        self.assertEqual(params["ckpt"], self.SDXL)
        self.assertEqual(notes, [])

    def test_explicit_existing_respected(self):
        from comfy_agent.model_adapt import adapt_ckpt
        k = _k([self.SDXL, "juggernautXL_v9.safetensors"])
        params, notes = adapt_ckpt(self.T2I(self.SDXL),
                                   {"ckpt": "juggernautXL_v9.safetensors"}, k)
        self.assertEqual(params["ckpt"], "juggernautXL_v9.safetensors")
        self.assertEqual(notes, [])

    def test_video_template_untouched(self):
        from comfy_agent.model_adapt import adapt_ckpt
        params, notes = adapt_ckpt(self.video, {}, _k([]))
        self.assertEqual(params, {})
        self.assertEqual(notes, [])

    def test_model_prefs_used_for_default(self):
        from comfy_agent.model_adapt import adapt_ckpt
        cps = ["juggernautXL_v9.safetensors", "my_fav_xl.safetensors"]
        with _patched_settings({"model_prefs":
                                {"sdxl": "my_fav_xl.safetensors"}}):
            params, _ = adapt_ckpt(self.T2I(self.SDXL), {}, _k(cps))
        self.assertEqual(params["ckpt"], "my_fav_xl.safetensors")


    def test_blank_ckpt_treated_as_unspecified(self):
        """大脑传 ckpt="" 时不得原样写进工作流（ckpt_name='' 必校验失败）。"""
        from comfy_agent.model_adapt import adapt_ckpt
        k = _k(["juggernautXL_v9.safetensors"])
        params, notes = adapt_ckpt(self.T2I(self.SDXL), {"ckpt": ""}, k)
        self.assertEqual(params["ckpt"], "juggernautXL_v9.safetensors")

    def test_blank_ckpt_default_present_drops_key(self):
        from comfy_agent.model_adapt import adapt_ckpt
        k = _k([self.SDXL])
        params, notes = adapt_ckpt(self.T2I(self.SDXL), {"ckpt": "   "}, k)
        self.assertNotIn("ckpt", params)


class RecordingClient:
    """记录提交的工作流（复用 test_runner.FakeClient 行为）。"""

    def __init__(self):
        from tests.test_runner import FakeClient
        self._inner = FakeClient()
        self.last_wf = None

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def prompt(self, wf):
        self.last_wf = wf
        return self._inner.prompt(wf)


class TestRunTemplateIntegration(unittest.TestCase):
    def test_friend_machine_t2i_auto_adapts(self):
        """朋友设备没有 novaAnimeXL，只有一个任意 SDXL 模型 → t2i 照常出图。"""
        from comfy_agent.runner import run_template
        snap = copy.deepcopy(SNAPSHOT)
        snap["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"] = \
            [["juggernautXL_v9.safetensors"], {}]
        k = _k(["juggernautXL_v9.safetensors"], snapshot=snap)
        client = RecordingClient()
        r = run_template("t2i", {"prompt": "1cat, orange cat, astronaut"},
                         knowledge=k, client=client, wait=False)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["stage"], "running")
        self.assertEqual(client.last_wf["1"]["inputs"]["ckpt_name"],
                         "juggernautXL_v9.safetensors")
        self.assertTrue(any("自动适配" in w for w in r.get("warnings", [])))

    def test_no_checkpoints_render_failed_with_hint(self):
        from comfy_agent.runner import run_template
        r = run_template("t2i", {"prompt": "x"}, knowledge=_k([]),
                         client=RecordingClient())
        self.assertEqual(r["stage"], "render_failed")
        self.assertTrue(r["missing_models"])
        self.assertIn("hint", r)
        self.assertIn("model_prefs", r["hint"])

    def test_user_explicit_missing_cross_family_reports_missing(self):
        """显式指定缺失模型且无可替换家族 → 报缺模型而不是静默换错模型。"""
        from comfy_agent.runner import run_template
        from comfy_agent.templates.image import SDXL_CKPT
        r = run_template("t2i", {"prompt": "x", "ckpt": SDXL_CKPT},
                         knowledge=_k(["dreamshaper_8.safetensors"]),
                         client=RecordingClient())
        self.assertEqual(r["stage"], "render_failed")
        self.assertTrue(r["missing_models"])

    def test_blank_ckpt_never_renders_empty_name(self):
        """ckpt="" 的端到端回归：工作流必须落到本机真实模型名。"""
        from comfy_agent.runner import run_template
        snap = copy.deepcopy(SNAPSHOT)
        snap["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"] = \
            [["juggernautXL_v9.safetensors"], {}]
        k = _k(["juggernautXL_v9.safetensors"], snapshot=snap)
        client = RecordingClient()
        r = run_template("t2i", {"prompt": "x", "ckpt": ""},
                         knowledge=k, client=client, wait=False)
        self.assertTrue(r["ok"], r)
        self.assertEqual(client.last_wf["1"]["inputs"]["ckpt_name"],
                         "juggernautXL_v9.safetensors")


if __name__ == "__main__":
    unittest.main(verbosity=2)
