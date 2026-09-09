# -*- coding: utf-8 -*-
"""单元测试：转换器 / 校验器 / 修复层 / 模板 / 评估（不依赖服务器与 LLM）。

运行：
  cd comfy-agent
  <整合包python> -B -m unittest discover -s tests -v
"""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 测试数据：用户4个真实工作流（存在性检查后跳过缺失的）
WF_DIR = Path(r"D:\comfiUI\ComfyUI-aki\ComfyUI-aki-v3\ComfyUI\user\default\workflows")


def _fake_knowledge():
    """不依赖服务器/磁盘的知识层替身。"""
    from comfy_agent.knowledge import Knowledge
    snapshot = {
        "KSampler": {"input": {"required": {
            "model": ["MODEL", {}],
            "seed": ["INT", {"default": 0, "min": 0, "max": 2**64,
                              "control_after_generate": True}],
            "steps": ["INT", {"default": 20, "min": 1, "max": 10000}],
            "cfg": ["FLOAT", {"default": 8.0, "min": 0.0, "max": 100.0}],
            "sampler_name": [["euler", "dpmpp_2m"], {}],
            "scheduler": [["simple", "karras"], {}],
            "positive": ["CONDITIONING", {}],
            "negative": ["CONDITIONING", {}],
            "latent_image": ["LATENT", {}],
            "denoise": ["FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0}],
        }}, "input_order": {"required": [
            "model", "seed", "steps", "cfg", "sampler_name", "scheduler",
            "positive", "negative", "latent_image", "denoise"]}},
        "CheckpointLoaderSimple": {"input": {"required": {
            "ckpt_name": [["m1.safetensors", "m2.safetensors"], {}]}},
            "input_order": {"required": ["ckpt_name"]}},
        "Reroute": {},   # 不应被用到（画布原语）
    }
    ext_map = {"FakeNode": ["some-package"]}
    models = {"checkpoints": ["m1.safetensors", "m2.safetensors"],
              "vae": ["sdxl_vae.safetensors"]}
    return Knowledge(snapshot, ext_map, models)


class TestConvertWidgetMapping(unittest.TestCase):
    """KSampler widget 序列映射（含 randomize 伴随值跳过）。"""

    def _ui(self):
        return {"nodes": [
            {"id": 1, "type": "CheckpointLoaderSimple", "mode": 0,
             "inputs": [], "widgets_values": ["m1.safetensors"]},
            {"id": 2, "type": "KSampler", "mode": 0,
             "inputs": [
                 {"name": "model", "link": 1},
                 {"name": "positive", "link": None},
                 {"name": "negative", "link": None},
                 {"name": "latent_image", "link": None},
                 {"name": "seed", "link": None, "widget": {"name": "seed"}},
             ],
             "widgets_values": [42, "randomize", 20, 7.5, "euler", "simple", 0.8]},
        ], "links": [[1, 1, 0, 2, 0, "MODEL"]]}

    def test_widgets_aligned(self):
        from comfy_agent.convert import convert_ui_to_api
        api = convert_ui_to_api(self._ui(), _fake_knowledge())
        ks = api["2"]["inputs"]
        self.assertEqual(ks["seed"], 42)
        self.assertEqual(ks["steps"], 20)
        self.assertEqual(ks["cfg"], 7.5)
        self.assertEqual(ks["sampler_name"], "euler")
        self.assertEqual(ks["scheduler"], "simple")
        self.assertEqual(ks["denoise"], 0.8)
        self.assertNotIn("randomize", ks.values())

    def test_reroute_transparent(self):
        from comfy_agent.convert import convert_ui_to_api
        ui = self._ui()
        # 插入 Reroute：link 1 改为 ckpt->reroute，reroute->ksampler
        ui["nodes"].append({"id": 9, "type": "Reroute", "mode": 0,
                            "inputs": [{"name": "", "link": 1}],
                            "outputs": [{"name": "", "links": [2]}]})
        ui["links"] = [[1, 1, 0, 9, 0, "MODEL"], [2, 9, 0, 2, 0, "MODEL"]]
        api = convert_ui_to_api(ui, _fake_knowledge())
        self.assertEqual(api["2"]["inputs"]["model"], ["1", 0])

    def test_bypass_removed(self):
        from comfy_agent.convert import convert_ui_to_api
        ui = self._ui()
        ui["nodes"][1]["mode"] = 4   # bypass KSampler
        api = convert_ui_to_api(ui, _fake_knowledge())
        self.assertNotIn("2", api)


class TestValidate(unittest.TestCase):
    def test_bad_enum_and_range(self):
        from comfy_agent.validate import validate_workflow
        k = _fake_knowledge()
        api = {"1": {"class_type": "KSampler", "inputs": {
            "model": ["9", 0], "seed": 1, "steps": 99999, "cfg": 5.0,
            "sampler_name": "nonexistent", "scheduler": "simple",
            "positive": ["9", 0], "negative": ["9", 0],
            "latent_image": ["9", 0], "denoise": 1.0}}}
        issues = validate_workflow(api, k)
        kinds = {i.kind for i in issues}
        self.assertIn("bad_enum", kinds)
        self.assertIn("out_of_range", kinds)
        self.assertIn("bad_link", kinds)   # 节点9不存在

    def test_family_safe_enum(self):
        """跨家族模型不能互相替换（SDXL VAE 不能变 minimax VAE）。"""
        from comfy_agent.validate import _closest_same_family
        choices = ["minimax_audio_vae.safetensors", "minimax_video_vae.safetensors"]
        self.assertIsNone(
            _closest_same_family("sdxl/sdxlVAE.safetensors", choices))

    def test_missing_node_hint(self):
        from comfy_agent.validate import validate_workflow
        k = _fake_knowledge()
        api = {"1": {"class_type": "NopeNode", "inputs": {}}}
        issues = validate_workflow(api, k)
        self.assertEqual(issues[0].kind, "missing_node")


class TestRepair(unittest.TestCase):
    def test_auto_repair_enum(self):
        from comfy_agent.repair import auto_repair
        k = _fake_knowledge()
        api = {"1": {"class_type": "KSampler", "inputs": {
            "model": ["9", 0], "seed": 1, "steps": 20, "cfg": 5.0,
            "sampler_name": "euler_typo", "scheduler": "simple",
            "positive": ["9", 0], "negative": ["9", 0],
            "latent_image": ["9", 0], "denoise": 1.0}},
            "9": {"class_type": "CheckpointLoaderSimple",
                  "inputs": {"ckpt_name": "m1.safetensors"}}}
        wf, report = auto_repair(api, k)
        fixed_inputs = [f for f in report.fixes if f["input"] == "sampler_name"]
        self.assertTrue(fixed_inputs)
        self.assertEqual(wf["1"]["inputs"]["sampler_name"], "euler")

    def test_vae_swap_to_checkpoint(self):
        from comfy_agent.repair import _swap_vae_to_checkpoint
        api = {
            "1": {"class_type": "CheckpointLoaderSimple",
                  "inputs": {"ckpt_name": "m1.safetensors"}},
            "2": {"class_type": "VAELoader",
                  "inputs": {"vae_name": "lost.safetensors"}},
            "3": {"class_type": "VAEDecode",
                  "inputs": {"samples": ["4", 0], "vae": ["2", 0]}},
        }
        ok = _swap_vae_to_checkpoint(api, "2")
        self.assertTrue(ok)
        self.assertNotIn("2", api)
        self.assertEqual(api["3"]["inputs"]["vae"], ["1", 2])  # ckpt槽2=VAE


class TestTemplates(unittest.TestCase):
    def test_all_templates_render(self):
        from comfy_agent.templates import all_templates
        from comfy_agent.validate import validate_workflow
        k = _fake_knowledge()
        for t in all_templates():
            with self.subTest(template=t.id):
                params = {"prompt": "test", "image": "x.png"}
                if t.id == "merge_videos":
                    params = {"video1": "a.mp4", "video2": "b.mp4"}
                elif t.id == "extract_frame":
                    params = {"video": "a.mp4", "frame_index": 0}
                wf = t.render(params)
                self.assertIsInstance(wf, dict)
                for v in wf.values():
                    self.assertIsInstance(v, dict)


class TestEvalTier0(unittest.TestCase):
    def test_png_size(self):
        from brain.eval import tier0_check
        # 生成 1x1 PNG（纯标准库）
        import struct, zlib
        def chunk(tag, data):
            c = tag + data
            return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))
        ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
        png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
               + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
               + chunk(b"IEND", b""))
        p = Path(__file__).parent / "_test.png"
        p.write_bytes(png)
        try:
            r = tier0_check(str(p), {"min_width": 4})
            self.assertFalse(r["pass"])      # 1 < 4
            r2 = tier0_check(str(p))
            self.assertTrue(r2["pass"])
        finally:
            p.unlink(missing_ok=True)


class TestJSONExtract(unittest.TestCase):
    def test_extract_from_codefence(self):
        from brain.llm import _extract_json
        raw = '评估如下\n```json\n{"pass": true, "score": 8, "issues": [], "advice": "ok"}\n```'
        d = _extract_json(raw)
        self.assertTrue(d["pass"])
        self.assertEqual(d["score"], 8)


if __name__ == "__main__":
    unittest.main(verbosity=2)
