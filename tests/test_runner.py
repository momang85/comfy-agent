# -*- coding: utf-8 -*-
"""通用执行引擎五段状态机单测（mock 服务器，不烧 GPU）。"""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.test_synth import SNAPSHOT, _k  # noqa: E402


def _wf_ok():
    return {"1": {"class_type": "CheckpointLoaderSimple",
                  "inputs": {"ckpt_name": "m1.safetensors"}},
            "2": {"class_type": "CLIPTextEncode",
                  "inputs": {"text": "pos", "clip": ["1", 1]}},
            "3": {"class_type": "CLIPTextEncode",
                  "inputs": {"text": "neg", "clip": ["1", 1]}},
            "4": {"class_type": "EmptyLatentImage",
                  "inputs": {"width": 512, "height": 512, "batch_size": 1}},
            "5": {"class_type": "KSampler",
                  "inputs": {"model": ["1", 0], "positive": ["2", 0],
                             "negative": ["3", 0], "latent_image": ["4", 0],
                             "seed": 0, "steps": 20, "cfg": 7.0,
                             "sampler_name": "euler", "scheduler": "simple",
                             "denoise": 1.0}},
            "6": {"class_type": "VAEDecode",
                  "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
            "7": {"class_type": "SaveImage",
                  "inputs": {"images": ["6", 0], "filename_prefix": "t"}}}


class FakeClient:
    """mock 服务器：prompt 接受一切，history 立刻完成，输出假图。"""

    def __init__(self, reject: bool = False, exec_error: bool = False):
        self.reject = reject
        self.exec_error = exec_error
        self.prompt_calls = 0

    def prompt(self, wf):
        self.prompt_calls += 1
        if self.reject:
            raise Exception("should not reach")
        return {"prompt_id": "pid1", "node_errors": {}}

    def wait_for_result(self, pid, timeout=1800, on_status=None):
        if self.exec_error:
            return {"status": {"completed": True, "status_str": "error",
                               "messages": [["execution_error",
                                             {"node_id": "5",
                                              "node_type": "KSampler",
                                              "exception_message":
                                                  "CUDA out of memory"}]]},
                    "outputs": {}}
        return {"status": {"completed": True, "status_str": "success",
                           "messages": []},
                "outputs": {"7": {"images": [
                    {"filename": "out.png", "subfolder": "",
                     "type": "output"}]}}}

    def outputs_of(self, entry, save_dir=None):
        return [{"filename": "out.png", "type": "output",
                 "local_path": str(save_dir / "out.png")
                 if save_dir else None}]

    def history(self, pid=None):
        return {"pid1": self.wait_for_result(pid)}

    def system_stats(self):
        return {}

    def vram_free_gb(self):
        return 1.0


class TestFiveStage(unittest.TestCase):
    def test_validation_failed_structured(self):
        from comfy_agent.runner import run_workflow
        bad = {"1": {"class_type": "NoSuchNode", "inputs": {}}}
        r = run_workflow(bad, knowledge=_k(), client=FakeClient(), wait=False)
        self.assertEqual(r["stage"], "validation_failed")
        self.assertEqual(r["validation_issues"][0]["kind"], "missing_node")
        self.assertIn("hint", r)

    def test_completed_pipeline(self):
        from comfy_agent.runner import run_workflow
        with mock.patch("comfy_agent.runner.config.RESULTS_DIR",
                        Path(__file__).parent / "_out"):
            r = run_workflow(_wf_ok(), source="test", knowledge=_k(),
                             client=FakeClient(), wait=True)
        self.assertEqual(r["stage"], "completed")
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["outputs"]), 1)

    def test_execution_failed_with_suggestion(self):
        from comfy_agent.runner import run_workflow
        r = run_workflow(_wf_ok(), knowledge=_k(),
                         client=FakeClient(exec_error=True), wait=True)
        self.assertEqual(r["stage"], "execution_failed")
        self.assertIn("exec_error", r)
        self.assertIn("suggestion", r)
        self.assertIn("hint", r)

    def test_wait_false_returns_running(self):
        from comfy_agent.runner import run_workflow
        r = run_workflow(_wf_ok(), knowledge=_k(), client=FakeClient(),
                         wait=False)
        self.assertEqual(r["stage"], "running")
        self.assertTrue(r["ok"])
        self.assertIn("prompt_id", r)

    def test_run_template_unknown(self):
        from comfy_agent.runner import run_template
        r = run_template("no_such_template", {}, knowledge=_k(),
                         client=FakeClient())
        self.assertEqual(r["stage"], "render_failed")


class TestLoadWorkflowTool(unittest.TestCase):
    def test_load_converts_and_reports(self):
        from brain.tools import ToolContext, execute_tool
        ctx = ToolContext()
        # 真实用户工作流
        p = (r"D:\comfiUI\ComfyUI-aki\ComfyUI-aki-v3\ComfyUI\user\default"
             r"\workflows\图生图.json")
        if not Path(p).exists():
            self.skipTest("用户工作流不在")
        r = execute_tool(ctx, "load_workflow", {"path": p})
        self.assertTrue(r["ok"])
        self.assertGreater(r["nodes"], 0)
        self.assertIn("validation_issues", r)


class TestForcedVideoEvaluation(unittest.TestCase):
    """视频产物强制评估（引擎层保底）。"""

    def test_video_output_triggers_evaluation(self):
        from comfy_agent import runner as R
        outs = [{"filename": "v.mp4", "type": "output",
                 "local_path": "x/v.mp4"}]
        fake_eval = mock.Mock()
        fake_eval.to_dict.return_value = {"tier0": [], "vlm": [
            {"pass": True, "score": 8, "issues": []}], "ok": True}
        result = {"ok": True}
        with mock.patch("comfy_agent.runner.config.EVAL_POLICY", "auto"), \
             mock.patch("brain.eval.evaluate_video",
                        return_value=fake_eval) as ev_fn, \
             mock.patch("brain.events.emit") as em:
            R._force_video_evaluation(result, outs)
        self.assertTrue(ev_fn.called)
        self.assertIn("evaluation", result)
        self.assertEqual(result["evaluation"]["verdict"], True)
        self.assertEqual(em.call_args[0][1]["kind"], "video_forced")

    def test_image_output_no_evaluation(self):
        from comfy_agent import runner as R
        result = {"ok": True}
        with mock.patch("comfy_agent.runner.config.EVAL_POLICY", "auto"), \
             mock.patch("brain.eval.evaluate_video") as ev_fn:
            R._force_video_evaluation(
                result, [{"local_path": "x/img.png"}])
        self.assertFalse(ev_fn.called)
        self.assertNotIn("evaluation", result)

    def test_policy_off_skips(self):
        from comfy_agent import runner as R
        result = {"ok": True}
        with mock.patch("comfy_agent.runner.config.EVAL_POLICY", "off"), \
             mock.patch("brain.eval.evaluate_video") as ev_fn:
            R._force_video_evaluation(
                result, [{"local_path": "x/v.mp4"}])
        self.assertFalse(ev_fn.called)

    def test_eval_exception_does_not_break_delivery(self):
        from comfy_agent import runner as R
        result = {"ok": True}
        with mock.patch("comfy_agent.runner.config.EVAL_POLICY", "auto"), \
             mock.patch("brain.eval.evaluate_video",
                        side_effect=RuntimeError("ffmpeg炸了")):
            R._force_video_evaluation(
                result, [{"local_path": "x/v.mp4"}])
        self.assertIn("evaluation", result)
        self.assertIn("error", result["evaluation"])


class TestForcedImageEvaluation(unittest.TestCase):
    """图像产物强制评估（引擎层保底：轻量大脑常跳过 view_image）。"""

    def test_image_output_triggers_evaluation(self):
        from comfy_agent import runner as R
        outs = [{"filename": "i.png", "type": "output", "local_path": "x/i.png"}]
        fake_eval = mock.Mock()
        fake_eval.to_dict.return_value = {"tier0": [], "ok": True, "vlm": [
            {"pass": True, "score": 9, "issues": [], "image": "x/i.png"}]}
        result = {"ok": True}
        with mock.patch("comfy_agent.runner.config.EVAL_POLICY", "auto"), \
             mock.patch("brain.eval.evaluate", return_value=fake_eval) as ev_fn, \
             mock.patch("brain.events.emit") as em:
            R._force_image_evaluation(result, outs)
        self.assertTrue(ev_fn.called)
        self.assertIn("evaluation", result)
        self.assertEqual(result["evaluation"]["verdict"], True)
        self.assertEqual(em.call_args[0][1]["kind"], "image_forced")

    def test_video_only_no_image_eval(self):
        from comfy_agent import runner as R
        result = {"ok": True}
        with mock.patch("comfy_agent.runner.config.EVAL_POLICY", "auto"), \
             mock.patch("brain.eval.evaluate") as ev_fn:
            R._force_image_evaluation(result, [{"local_path": "x/v.mp4"}])
        self.assertFalse(ev_fn.called)
        self.assertNotIn("evaluation", result)

    def test_image_policy_off_skips(self):
        from comfy_agent import runner as R
        result = {"ok": True}
        with mock.patch("comfy_agent.runner.config.EVAL_POLICY", "off"), \
             mock.patch("brain.eval.evaluate") as ev_fn:
            R._force_image_evaluation(result, [{"local_path": "x/i.png"}])
        self.assertFalse(ev_fn.called)

    def test_eval_exception_does_not_break_delivery(self):
        from comfy_agent import runner as R
        result = {"ok": True}
        with mock.patch("comfy_agent.runner.config.EVAL_POLICY", "auto"), \
             mock.patch("brain.eval.evaluate",
                        side_effect=RuntimeError("VLM 炸了")):
            R._force_image_evaluation(result, [{"local_path": "x/i.png"}])
        self.assertIn("error", result["evaluation"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
