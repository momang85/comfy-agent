# -*- coding: utf-8 -*-
"""生成质量护栏单测：视频帧/秒换算、CFG 收敛、负面词合并、提示词体检、
调度器参数、compose 加载器去重。

对应实测发现的四类质量问题：
  1) length=5 被当成"5 秒"（实际 5 帧）
  2) 视频 turbo 模型 cfg 被写到 7.5（推荐 3.0）
  3) 大脑自写负面词丢掉族默认（watermark/signature/质量词）
  4) 给 Qwen3VL 视频模型写 SDXL 标签堆砌；正向缺质量词前缀
"""
import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.test_synth import SNAPSHOT  # noqa: E402


def _k(checkpoints, snapshot=None):
    from comfy_agent.knowledge import Knowledge
    return Knowledge(snapshot or SNAPSHOT, {}, {"checkpoints": list(checkpoints)})


class RecordingClient:
    """记录提交的工作流（复用 test_runner.FakeClient 的服务器模拟）。"""

    def __init__(self):
        from tests.test_runner import FakeClient
        self._inner = FakeClient()
        self.last_wf = None

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def prompt(self, wf):
        self.last_wf = wf
        return self._inner.prompt(wf)


class TestVideoLengthUnits(unittest.TestCase):
    def test_seconds_to_frames(self):
        from comfy_agent.templates.video import _to_frames
        self.assertEqual(_to_frames("5s")[0], 120)
        self.assertEqual(_to_frames(5)[0], 120)        # 裸值 <24 视为秒
        self.assertEqual(_to_frames(124), (124, False))  # 已是帧，原样
        self.assertEqual(_to_frames("2.5s")[0], 60)

    def test_normalize_seconds_and_alias(self):
        from comfy_agent.templates.video import MiniMaxT2V
        t = MiniMaxT2V()
        out, notes = t.normalize_params({"prompt": "x", "length": 5})
        self.assertEqual(out["length"], 120)
        self.assertTrue(notes)
        out2, _ = t.normalize_params({"prompt": "x", "seconds": 5})
        self.assertEqual(out2["length"], 120)          # 别名换算
        out3, notes3 = t.normalize_params({"prompt": "x", "length": 124})
        self.assertEqual(out3["length"], 124)
        self.assertEqual(notes3, [])

    def test_ltx_also_normalizes(self):
        from comfy_agent.templates.video import LTXVideo
        out, notes = LTXVideo().normalize_params({"prompt": "x", "duration": 4})
        self.assertEqual(out["length"], 96)
        self.assertTrue(notes)


class TestParamGuard(unittest.TestCase):
    def test_video_cfg_converged(self):
        from comfy_agent.runner import _guard_params
        from comfy_agent.templates.video import MiniMaxT2V
        params, notes = _guard_params(MiniMaxT2V(), {"cfg": 7.5})
        self.assertEqual(params["cfg"], 3.0)
        self.assertTrue(notes)

    def test_cfg_within_range_untouched(self):
        from comfy_agent.runner import _guard_params
        from comfy_agent.templates.video import MiniMaxT2V
        params, notes = _guard_params(MiniMaxT2V(), {"cfg": 3.5})
        self.assertEqual(params["cfg"], 3.5)
        self.assertEqual(notes, [])

    def test_sdxl_cfg_untouched(self):
        from comfy_agent.runner import _guard_params
        from comfy_agent.templates.image import T2I, SDXL_CKPT
        params, notes = _guard_params(T2I(SDXL_CKPT), {"cfg": 7.0})
        self.assertEqual(params["cfg"], 7.0)
        self.assertEqual(notes, [])


class TestPromptSpec(unittest.TestCase):
    def test_negative_merged_with_default(self):
        from comfy_agent.promptspec import prepare
        prompt, neg, warns = prepare("sdxl", "masterpiece, best quality, 1cat",
                                     "blurry, low quality")
        self.assertIn("watermark", neg)
        self.assertTrue(any("默认负面" in w for w in warns))

    def test_complete_negative_kept(self):
        from comfy_agent.promptspec import merge_negative, negative_for
        base = negative_for("sdxl")
        out, changed = merge_negative("sdxl", base)
        self.assertFalse(changed)
        self.assertEqual(out, base)

    def test_quality_prefix_added(self):
        from comfy_agent.promptspec import check_prompt
        chk = check_prompt("sdxl", "1girl, cat ears", "")
        self.assertTrue(chk["prompt"].startswith("masterpiece, best quality"))
        self.assertTrue(chk["warnings"])

    def test_video_tag_soup_warned(self):
        from comfy_agent.promptspec import check_prompt
        chk = check_prompt(
            "minimax",
            "gentle movement, smooth animation, natural pose, soft lighting", "")
        self.assertTrue(chk["warnings"])
        self.assertTrue(any("自然语言" in w or "动作/镜头" in w
                            for w in chk["warnings"]))

    def test_style_contradiction_warned(self):
        from comfy_agent.promptspec import check_prompt
        chk = check_prompt("sdxl", "Studio Ghibli anime style, tree",
                           "photorealistic, cartoon style")
        self.assertTrue(any("冲突" in w for w in chk["warnings"]))

    def test_realistic_vs_anime_conflict(self):
        from comfy_agent.promptspec import check_prompt
        chk = check_prompt("sdxl", "anime style, realistic skin texture", "")
        self.assertTrue(any("自相矛盾" in w for w in chk["warnings"]))


class TestRunTemplateQuality(unittest.TestCase):
    def _friend_knowledge(self, model="juggernautXL_v9.safetensors"):
        snap = copy.deepcopy(SNAPSHOT)
        snap["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"] = \
            [[model], {}]
        return _k([model], snapshot=snap)

    def test_prefix_and_negative_applied(self):
        from comfy_agent.runner import run_template
        client = RecordingClient()
        r = run_template("t2i", {"prompt": "1cat, orange fur",
                                 "negative": "blurry"},
                         knowledge=self._friend_knowledge(), client=client,
                         wait=False)
        self.assertTrue(r["ok"], r)
        warns = " ".join(r.get("warnings", []))
        self.assertIn("质量词前缀", warns)
        self.assertIn("默认负面", warns)
        # 渲染结果确实带上了质量前缀与合并后的负面词
        wf = client.last_wf
        self.assertTrue(wf["2"]["inputs"]["text"].startswith("masterpiece"))
        self.assertIn("watermark", wf["3"]["inputs"]["text"])

    def test_low_resolution_warned(self):
        from comfy_agent.runner import run_template
        client = RecordingClient()
        r = run_template("t2i", {"prompt": "1cat", "width": 512, "height": 512},
                         knowledge=self._friend_knowledge(), client=client,
                         wait=False)
        self.assertTrue(r["ok"], r)
        warns = " ".join(r.get("warnings", []))
        self.assertIn("低于 SDXL", warns)

    def test_video_cfg_and_length_converged_end_to_end(self):
        from comfy_agent.runner import run_template
        client = RecordingClient()
        r = run_template("minimax_t2v",
                         {"prompt": "一只橘猫在太空舱里缓慢漂浮，镜头缓缓推近",
                          "length": 5, "cfg": 7.5},
                         knowledge=_k([]), client=client, wait=False)
        # 视频模板缺模型 → render_failed，但参数收敛结果必须回流（不静默丢弃）
        warns = " ".join(r.get("warnings", []))
        self.assertIn("按秒理解", warns)
        self.assertIn("cfg", warns)


class TestSchedulerAndCompose(unittest.TestCase):
    def test_scheduler_param_default_karras(self):
        from comfy_agent.templates.image import T2I, SDXL_CKPT
        t = T2I(SDXL_CKPT)
        self.assertIn("scheduler", {p.name for p in t.params()})
        wf = t.render({"prompt": "1cat"})
        self.assertEqual(wf["5"]["inputs"]["scheduler"], "karras")

    def test_compose_dedupes_shared_loader(self):
        from comfy_agent.synth.compose import compose
        from comfy_agent.templates.image import T2I, UpscalePass, SDXL_CKPT
        a = T2I(SDXL_CKPT).render({"prompt": "1cat"})
        b = UpscalePass().render({"image": "in.png", "prompt": "1cat"})
        merged = compose(a, b, _k([SDXL_CKPT]))
        ckpts = [n for n in merged.values()
                 if n["class_type"] == "CheckpointLoaderSimple"]
        self.assertEqual(len(ckpts), 1, merged)
        # 去重后仍应通过结构校验（无悬空连线）
        from comfy_agent.validate import validate_workflow
        issues = [i for i in validate_workflow(merged, _k([SDXL_CKPT]))
                  if i.kind in ("bad_link", "missing_node")]
        self.assertEqual(issues, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
