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
from comfy_agent.knowledge import Knowledge  # noqa: E402
from comfy_agent.validate import validate_workflow  # noqa: E402


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

    def test_hires_chain(self):
        """hires=1.5 时：潜空间放大 + 二段低 denoise + tiled 解码。"""
        from comfy_agent.templates.image import T2I, SDXL_CKPT
        wf = T2I(SDXL_CKPT).render({"prompt": "1cat", "hires": 1.5})
        self.assertIn("LatentUpscaleBy", {n["class_type"] for n in wf.values()})
        self.assertEqual(wf["7"]["inputs"]["denoise"], 0.4)
        self.assertEqual(wf["7"]["inputs"]["latent_image"], ["6", 0])
        self.assertEqual(wf["8"]["class_type"], "VAEDecodeTiled")

    def test_no_hires_plain_decode(self):
        from comfy_agent.templates.image import T2I, SDXL_CKPT
        wf = T2I(SDXL_CKPT).render({"prompt": "1cat"})
        self.assertEqual(wf["8"]["class_type"], "VAEDecode")
        self.assertNotIn("6", wf)

    def test_style_prompt_split_conditioning(self):
        """style_prompt 独立编码 + ConditioningCombine（绕开 77 token 截断）。"""
        from comfy_agent.templates.image import T2I, SDXL_CKPT
        wf = T2I(SDXL_CKPT).render({"prompt": "1cat",
                                    "style_prompt": "golden hour, rim light"})
        self.assertEqual(wf["2c"]["class_type"], "ConditioningCombine")
        self.assertEqual(wf["5"]["inputs"]["positive"], ["2c", 0])

    def test_inpaint_template_renders(self):
        from comfy_agent.templates import get_template
        t = get_template("inpaint")
        self.assertIsNotNone(t)
        wf = t.render({"image": "in.png", "mask": "m.png", "prompt": "clean bg"})
        classes = {n["class_type"] for n in wf.values()}
        self.assertIn("VAEEncodeForInpaint", classes)
        issues = [i for i in validate_workflow(wf, _k([]))
                  if i.kind in ("missing_node", "bad_link", "type_mismatch")]
        self.assertEqual(issues, [])

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


class TestBlockCoverage(unittest.TestCase):
    def test_missing_blocks_reported(self):
        from comfy_agent.promptspec import block_coverage
        cov = block_coverage("sdxl", "masterpiece, 1girl, sitting")
        self.assertIn("lighting", cov["missing"])
        self.assertTrue(cov["suggest"]["lighting"])

    def test_full_prompt_covers_all(self):
        from comfy_agent.promptspec import block_coverage
        p = ("masterpiece, 1girl, detailed eyes, sitting, cafe, golden hour, "
             "cozy atmosphere, portrait, anime style")
        cov = block_coverage("sdxl", p)
        self.assertEqual(cov["missing"], [])

    def test_check_prompt_warns_incomplete(self):
        from comfy_agent.promptspec import check_prompt
        chk = check_prompt("sdxl", "1girl, sitting", "")
        self.assertTrue(any("维度不完整" in w for w in chk["warnings"]))


class TestNodePrefs(unittest.TestCase):
    def test_canny_available(self):
        from comfy_agent.nodes_prefs import resolve, CAPABILITIES
        cap = next(c for c in CAPABILITIES if c["id"] == "composition_lock")
        k = Knowledge(SNAPSHOT, {}, {"controlnet":
                                     ["controlnet++_union_sdxl_promax.safetensors"]})
        r = resolve(k, cap)
        self.assertTrue(r["ok"])
        self.assertEqual(r["class"], "Canny")

    def test_face_detailer_degrades(self):
        from comfy_agent.nodes_prefs import resolve, CAPABILITIES
        cap = next(c for c in CAPABILITIES if c["id"] == "face_fix")
        r = resolve(_k([]), cap)          # 无 face_yolov8m 模型
        self.assertFalse(r["ok"])
        self.assertIn("fallback", r)

    def test_summary_compact(self):
        from comfy_agent.nodes_prefs import capability_summary
        s = capability_summary(_k([]))
        self.assertIn("修脸", s)
        self.assertLessEqual(len(s.splitlines()), 15)


class TestTypeMismatch(unittest.TestCase):
    def test_wrong_type_link_flagged(self):
        """通用管线现在也查连线类型：IMAGE 输出接 MODEL 输入必须被拦。"""
        bad = {
            "1": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
            "2": {"class_type": "CheckpointLoaderSimple",
                  "inputs": {"ckpt_name": "m1.safetensors"}},
            "3": {"class_type": "KSampler", "inputs": {
                "model": ["1", 0],          # IMAGE 接 MODEL：类型错误
                "positive": ["4", 0], "negative": ["4", 0],
                "latent_image": ["5", 0], "seed": 0, "steps": 20,
                "cfg": 7.0, "sampler_name": "euler", "scheduler": "simple",
                "denoise": 1.0}},
            "4": {"class_type": "CLIPTextEncode",
                  "inputs": {"text": "x", "clip": ["2", 1]}},
            "5": {"class_type": "EmptyLatentImage",
                  "inputs": {"width": 512, "height": 512, "batch_size": 1}},
        }
        issues = [i for i in validate_workflow(bad, _k([]))
                  if i.kind == "type_mismatch"]
        self.assertTrue(issues)
        self.assertIn("MODEL", issues[0].message)


if __name__ == "__main__":
    unittest.main(verbosity=2)
