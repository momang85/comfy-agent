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
        # 4 秒 → 96 帧，但 LTXVImgToVideo 要求 8k+1（step=8）→ 对齐为 97
        self.assertEqual(out["length"], 97)
        self.assertTrue(notes)

    def test_ltx_grid_alignment(self):
        from comfy_agent.templates.video import LTXVideo
        out, notes = LTXVideo().normalize_params({"prompt": "x", "duration": 5})
        self.assertEqual(out["length"], 121)          # 120 → 8k+1 对齐
        self.assertTrue(any("网格" in n for n in notes), notes)

    def test_minimax_not_grid_snapped(self):
        """MiniMax 没有 8k+1 约束，不得被网格改动（只做秒→帧）。"""
        from comfy_agent.templates.video import MiniMaxT2V
        out, _ = MiniMaxT2V().normalize_params({"prompt": "x", "duration": 5})
        self.assertEqual(out["length"], 120)


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


class TestMultiToolCalls(unittest.TestCase):
    """一条消息多个工具调用：全部提取、按序执行、上限 3。"""

    def test_two_fences_parsed_in_order(self):
        from brain.agent import _parse_tool_calls
        reply = ('```json\n{"tool": "analyze_image", "args": {"path": "a.png"}}\n```\n'
                 '```json\n{"tool": "run_template", "args": {"template_id": "t2i"}}\n```')
        calls = _parse_tool_calls(reply)
        self.assertEqual([c[0] for c in calls],
                         ["analyze_image", "run_template"])

    def test_limit_three(self):
        from brain.agent import _parse_tool_calls
        parts = [f'```json\n{{"tool": "view_image", "args": {{"n": {i}}}}}\n```'
                 for i in range(5)]
        self.assertEqual(len(_parse_tool_calls("\n".join(parts))), 3)

    def test_bare_json_lines(self):
        from brain.agent import _parse_tool_calls
        calls = _parse_tool_calls(
            '{"tool": "list_models", "args": {}}\n'
            '{"tool": "view_image", "args": {}}')
        self.assertEqual([c[0] for c in calls], ["list_models", "view_image"])

    def test_text_fallback_single_call(self):
        from brain.agent import _parse_tool_calls
        calls = _parse_tool_calls("我认为应该用 run_template 来生成。")
        self.assertEqual(calls[0][0], "run_template")
        self.assertEqual(calls[0][1], {})

    def test_no_calls_returns_empty(self):
        from brain.agent import _parse_tool_calls
        self.assertEqual(_parse_tool_calls("好的，已完成，无需工具。"), [])


class TestHardwareGuard(unittest.TestCase):
    """GPU 温度熔断（实测渲染期可达 87°C）。"""

    def test_over_limit(self):
        from comfy_agent import guard
        self.assertTrue(guard.over_limit(85, 85))
        self.assertTrue(guard.over_limit(90, 85))
        self.assertFalse(guard.over_limit(70, 85))
        self.assertFalse(guard.over_limit(None, 85))      # 读不到温度不误熔断
        self.assertFalse(guard.over_limit(99, 0))         # 阈值 0 = 关闭

    def test_gpu_temp_readable_or_none(self):
        from comfy_agent import guard
        t = guard.gpu_temp_c()
        self.assertTrue(t is None or 0 < t < 120)


class TestInputAutoUpload(unittest.TestCase):
    """输入文件由引擎代传 /input（P0-4）。"""

    class FakeClient:
        def __init__(self):
            self.uploaded = []

        def upload_image(self, path):
            self.uploaded.append(str(path))
            return {"name": "uploaded_" + path.name}

    def test_local_path_is_uploaded_and_replaced(self):
        import tempfile
        from pathlib import Path as P
        from comfy_agent.runner import _ensure_inputs_uploaded
        from comfy_agent.templates import get_template
        with tempfile.TemporaryDirectory() as d:
            f = P(d) / "seg1.mp4"
            f.write_bytes(b"x")
            cli = self.FakeClient()
            tpl = get_template("extract_frame")
            params, notes, err = _ensure_inputs_uploaded(
                tpl, {"video": str(f), "frame_index": 3}, cli)
        self.assertIsNone(err)
        self.assertEqual(params["video"], "uploaded_seg1.mp4")
        self.assertEqual(cli.uploaded and len(cli.uploaded), 1)
        self.assertTrue(notes and "自动上传" in notes[0])

    def test_server_name_left_untouched(self):
        from comfy_agent.runner import _ensure_inputs_uploaded
        from comfy_agent.templates import get_template
        cli = self.FakeClient()
        params, notes, err = _ensure_inputs_uploaded(
            get_template("merge_videos"),
            {"video1": "seg1.mp4", "video2": "seg2.mp4"}, cli)
        self.assertIsNone(err)
        self.assertEqual(cli.uploaded, [])          # 已是 server 名，不再上传
        self.assertEqual(params["video1"], "seg1.mp4")

    def test_upload_failure_reported(self):
        from comfy_agent.runner import _ensure_inputs_uploaded
        from comfy_agent.templates import get_template

        class Boom:
            def upload_image(self, path):
                raise RuntimeError("连接被拒绝")

        import tempfile
        from pathlib import Path as P
        with tempfile.TemporaryDirectory() as d:
            f = P(d) / "a.mp4"
            f.write_bytes(b"x")
            params, notes, err = _ensure_inputs_uploaded(
                get_template("extract_frame"), {"video": str(f)}, Boom())
        self.assertIn("上传到 ComfyUI /input 失败", err)


class TestVideoOomSuggestion(unittest.TestCase):
    """视频 OOM 也要能自动降参（原先只认 EmptyLatent*）。"""

    def test_video_nodes_reduced(self):
        from comfy_agent.repair import suggest_for_execution_error
        api = {"1": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {
            "width": 768, "height": 448, "length": 124, "prompt": "x"}}}
        s = suggest_for_execution_error({"message": "CUDA out of memory"}, api)
        self.assertTrue(s and s.get("applied"), s)
        got = api["1"]["inputs"]
        self.assertLess(got["width"], 768)
        self.assertLessEqual(got["height"], 448)
        self.assertEqual(got["length"], 62)
        self.assertEqual(got["width"] % 32, 0)      # 视频分辨率需 32 对齐


class TestChoiceSnapAndEvalReuse(unittest.TestCase):
    """P1-1 非法选项回落模板推荐值；P1-2/P1-4 评估复用与 use_last 回退。"""

    def test_choice_snaps_to_template_default(self):
        from comfy_agent.templates.image import T2I, SDXL_CKPT
        out, notes = T2I(SDXL_CKPT).normalize_params(
            {"prompt": "x", "sampler": "dpmpp_2m_karras"})
        self.assertEqual(out["sampler"], "dpmpp_2m")     # 模板默认，而非相似串
        self.assertTrue(any("回落" in n for n in notes), notes)

    def test_valid_choice_untouched(self):
        from comfy_agent.templates.image import T2I, SDXL_CKPT
        out, notes = T2I(SDXL_CKPT).normalize_params(
            {"prompt": "x", "sampler": "euler"})
        self.assertEqual(out["sampler"], "euler")
        self.assertFalse(any("回落" in n for n in notes), notes)

    def test_engine_eval_reuse_when_passed(self):
        from brain.tools import ToolContext, _engine_eval_reuse
        ctx = ToolContext()
        ctx.draft_meta["last_eval"] = {"prompt_id": "p1", "verdict": True,
                                       "score": 9, "files": ["a.mp4"]}
        got = _engine_eval_reuse(ctx, ["a.mp4"])
        self.assertTrue(got and got["skipped"] and got["pass_overall"])

    def test_no_reuse_when_failed_or_mismatch(self):
        from brain.tools import ToolContext, _engine_eval_reuse
        ctx = ToolContext()
        ctx.draft_meta["last_eval"] = {"prompt_id": "p1", "verdict": False,
                                       "score": 4, "files": ["a.mp4"]}
        self.assertIsNone(_engine_eval_reuse(ctx, ["a.mp4"]))   # 未通过 → 允许复评
        ctx.draft_meta["last_eval"] = {"verdict": True, "files": ["other.mp4"]}
        self.assertIsNone(_engine_eval_reuse(ctx, ["a.mp4"]))   # 不是同一产物

    def test_latest_project_outputs_scans_project(self):
        import tempfile
        from pathlib import Path as P
        from brain.tools import ToolContext, _latest_project_outputs

        class Proj:
            def __init__(self, d):
                self._d = P(d)

            def outputs_dir(self):
                return self._d

        with tempfile.TemporaryDirectory() as d:
            sub = P(d) / "run1"
            sub.mkdir()
            (sub / "a.mp4").write_bytes(b"1")
            (sub / "b.png").write_bytes(b"2")
            ctx = ToolContext()
            ctx.project = Proj(d)
            vids = _latest_project_outputs(ctx, (".mp4",))
        self.assertEqual(len(vids), 1)
        self.assertTrue(vids[0].endswith("a.mp4"))


class TestLengthTrainedCap(unittest.TestCase):
    """length 超过模型训练帧数上界时收敛（服务器 bounds 管不到，实测踩过）。"""

    def test_minimax_capped_at_362(self):
        from comfy_agent.templates.video import MiniMaxT2V
        out, notes = MiniMaxT2V().normalize_params(
            {"prompt": "x", "duration": 20})          # 20 秒 → 480 帧
        self.assertEqual(out["length"], 362)
        self.assertTrue(any("训练帧数上界" in n for n in notes), notes)

    def test_ltx_capped_at_257(self):
        from comfy_agent.templates.video import LTXVideo
        out, notes = LTXVideo().normalize_params({"prompt": "x", "length": 400})
        self.assertEqual(out["length"], 257)
        self.assertTrue(any("训练帧数上界" in n for n in notes), notes)

    def test_within_range_untouched(self):
        from comfy_agent.templates.video import MiniMaxT2V
        out, notes = MiniMaxT2V().normalize_params({"prompt": "x", "length": 124})
        self.assertEqual(out["length"], 124)
        self.assertFalse(any("上界" in n for n in notes), notes)


class TestListOutputsTool(unittest.TestCase):
    """list_outputs：重启后按需查询项目产物，不再猜路径。"""

    def test_lists_recent_files(self):
        import tempfile
        from pathlib import Path as P
        from brain.tools import ToolContext, execute_tool

        class Proj:
            def __init__(self, d):
                self._d = P(d)

            def outputs_dir(self):
                return self._d

        with tempfile.TemporaryDirectory() as d:
            (P(d) / "a.mp4").write_bytes(b"1")
            (P(d) / "b.png").write_bytes(b"2")
            ctx = ToolContext()
            ctx.project = Proj(d)
            r = execute_tool(ctx, "list_outputs", {})
        self.assertTrue(r["ok"])
        self.assertEqual(r["count"], 2)
        self.assertEqual({f["name"] for f in r["files"]}, {"a.mp4", "b.png"})

    def test_empty_project_is_ok(self):
        import tempfile
        from pathlib import Path as P
        from brain.tools import ToolContext, execute_tool

        class Proj:
            def __init__(self, d):
                self._d = P(d)

            def outputs_dir(self):
                return self._d

        with tempfile.TemporaryDirectory() as d:
            ctx = ToolContext()
            ctx.project = Proj(d)
            r = execute_tool(ctx, "list_outputs", {})
        self.assertTrue(r["ok"])          # 空项目不算错误
        self.assertEqual(r["count"], 0)

    def test_tool_registered_for_llm(self):
        from brain.tools import tools_schema_for_llm
        self.assertIn("list_outputs", tools_schema_for_llm())


if __name__ == "__main__":
    unittest.main(verbosity=2)
