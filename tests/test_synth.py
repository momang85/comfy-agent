# -*- coding: utf-8 -*-
"""图合成引擎单测：类型校验/DAG环/回滚/端口检测/管线拼接/插入模式/合成会话。"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 轻量假知识层：只含单测所需节点
SNAPSHOT = {
    "CheckpointLoaderSimple": {
        "input": {"required": {"ckpt_name": [["m1.safetensors"], {}]}},
        "output": ["MODEL", "CLIP", "VAE"],
        "output_name": ["MODEL", "CLIP", "VAE"],
        "input_order": {"required": ["ckpt_name"]}},
    "CLIPTextEncode": {
        "input": {"required": {"text": ["STRING", {"default": ""}],
                               "clip": ["CLIP", {}]}},
        "output": ["CONDITIONING"], "output_name": ["CONDITIONING"],
        "input_order": {"required": ["text", "clip"]}},
    "EmptyLatentImage": {
        "input": {"required": {
            "width": ["INT", {"default": 512}],
            "height": ["INT", {"default": 512}],
            "batch_size": ["INT", {"default": 1}]}},
        "output": ["LATENT"], "output_name": ["LATENT"],
        "input_order": {"required": ["width", "height", "batch_size"]}},
    "KSampler": {
        "input": {"required": {
            "model": ["MODEL", {}], "seed": ["INT", {"default": 0}],
            "steps": ["INT", {"default": 20}], "cfg": ["FLOAT", {"default": 8.0}],
            "sampler_name": [["euler"], {}], "scheduler": [["simple"], {}],
            "positive": ["CONDITIONING", {}], "negative": ["CONDITIONING", {}],
            "latent_image": ["LATENT", {}], "denoise": ["FLOAT", {"default": 1.0}]}},
        "output": ["LATENT"], "output_name": ["LATENT"],
        "input_order": {"required": ["model", "seed", "steps", "cfg",
                                     "sampler_name", "scheduler", "positive",
                                     "negative", "latent_image", "denoise"]}},
    "VAEDecode": {
        "input": {"required": {"samples": ["LATENT", {}], "vae": ["VAE", {}]}},
        "output": ["IMAGE"], "output_name": ["IMAGE"],
        "input_order": {"required": ["samples", "vae"]}},
    "VAEEncode": {
        "input": {"required": {"pixels": ["IMAGE", {}], "vae": ["VAE", {}]}},
        "output": ["LATENT"], "output_name": ["LATENT"],
        "input_order": {"required": ["pixels", "vae"]}},
    "SaveImage": {
        "input": {"required": {"images": ["IMAGE", {}],
                               "filename_prefix": ["STRING", {"default": "x"}]}},
        "output": [], "output_name": [],
        "input_order": {"required": ["images", "filename_prefix"]}},
    "LoadImage": {
        "input": {"required": {"image": [["a.png", "b.png"], {}]}},
        "output": ["IMAGE", "MASK"], "output_name": ["IMAGE", "MASK"],
        "input_order": {"required": ["image"]}},
    "ImageScaleBy": {
        "input": {"required": {"image": ["IMAGE", {}],
                               "upscale_method": [["lanczos"], {}],
                               "scale_by": ["FLOAT", {"default": 2.0}]}},
        "output": ["IMAGE"], "output_name": ["IMAGE"],
        "input_order": {"required": ["image", "upscale_method", "scale_by"]}},
    "LoraLoaderModelOnly": {
        "input": {"required": {"model": ["MODEL", {}],
                               "lora_name": [["l1.safetensors"], {}],
                               "strength_model": ["FLOAT", {"default": 1.0}]}},
        "output": ["MODEL"], "output_name": ["MODEL"],
        "input_order": {"required": ["model", "lora_name", "strength_model"]}},
    "Canny": {
        "input": {"required": {"image": ["IMAGE", {}],
                               "low_threshold": ["FLOAT", {"default": 0.4}],
                               "high_threshold": ["FLOAT", {"default": 0.8}]}},
        "output": ["IMAGE"], "output_name": ["IMAGE"],
        "input_order": {"required": ["image", "low_threshold", "high_threshold"]}},
    "ControlNetLoader": {
        "input": {"required": {"control_net_name": [["cn.safetensors"], {}]}},
        "output": ["CONTROL_NET"], "output_name": ["CONTROL_NET"],
        "input_order": {"required": ["control_net_name"]}},
    "ControlNetApplyAdvanced": {
        "input": {"required": {
            "positive": ["CONDITIONING", {}], "negative": ["CONDITIONING", {}],
            "control_net": ["CONTROL_NET", {}], "image": ["IMAGE", {}],
            "strength": ["FLOAT", {"default": 0.8}],
            "start_percent": ["FLOAT", {"default": 0.0}],
            "end_percent": ["FLOAT", {"default": 1.0}]}},
        "output": ["CONDITIONING", "CONDITIONING"],
        "output_name": ["positive", "negative"],
        "input_order": {"required": ["positive", "negative", "control_net",
                                     "image", "strength", "start_percent",
                                     "end_percent"]}},
}


def _k():
    from comfy_agent.knowledge import Knowledge
    return Knowledge(SNAPSHOT, {}, {})


def _t2i():
    return {
        "1": {"class_type": "CheckpointLoaderSimple",
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
              "inputs": {"images": ["6", 0], "filename_prefix": "x"}},
    }


class TestGraphOps(unittest.TestCase):
    def test_type_mismatch_rejected(self):
        from comfy_agent.synth.graph import Graph
        from comfy_agent.synth import ops
        g = Graph(_t2i(), _k())
        with self.assertRaises(ops.EditError) as cm:
            ops.connect(g, "1", 1, "5", "model")   # CLIP -> MODEL
        self.assertEqual(cm.exception.diagnostics["reason"], "type_mismatch")
        self.assertEqual(g.api["5"]["inputs"]["model"], ["1", 0])  # 未被污染

    def test_cycle_rejected(self):
        from comfy_agent.synth.graph import Graph
        from comfy_agent.synth import ops
        g = Graph(_t2i(), _k())
        # 通过 API 直接造真实环：2.clip->3 且 3.clip->2（互指成环）
        g.api["2"]["inputs"]["clip"] = ["3", 0]
        g.api["3"]["inputs"]["clip"] = ["2", 0]
        self.assertTrue(g.has_cycle())

    def test_add_node_autofills_defaults(self):
        from comfy_agent.synth.graph import Graph
        from comfy_agent.synth import ops
        g = Graph({}, _k())
        ops.add_node(g, "1", "KSampler")
        inp = g.api["1"]["inputs"]
        self.assertEqual(inp["steps"], 20)
        self.assertEqual(inp["sampler_name"], "euler")
        self.assertIsNone(inp["model"])           # 连线型留空待接

    def test_insert_between(self):
        from comfy_agent.synth.graph import Graph
        from comfy_agent.synth import ops
        g = Graph(_t2i(), _k())
        ops.insert_between(g, "1", 0, "5", "model", "8",
                           "LoraLoaderModelOnly", wire_out="model",
                           wire_in="model",
                           extra={"lora_name": "l1.safetensors"})
        self.assertEqual(g.api["8"]["inputs"]["model"], ["1", 0])
        self.assertEqual(g.api["5"]["inputs"]["model"], ["8", 0])


class TestValidateGraph(unittest.TestCase):
    def test_diagnostics(self):
        from comfy_agent.synth.graph import Graph
        from comfy_agent.synth.validate_graph import validate_graph
        g = Graph(_t2i(), _k())
        g.api["9"] = {"class_type": "NoSuchNode", "inputs": {}}
        g.api["5"]["inputs"]["model"] = ["99", 0]   # 悬空引用
        issues = validate_graph(g)
        reasons = {i["reason"] for i in issues}
        self.assertIn("unknown_node", reasons)
        self.assertIn("dangling_link", reasons)


class TestPortsAndCompose(unittest.TestCase):
    def _upscale(self):
        return {
            "1": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
            "2": {"class_type": "ImageScaleBy",
                  "inputs": {"image": ["1", 0], "upscale_method": "lanczos",
                             "scale_by": 2.0}},
            "3": {"class_type": "CheckpointLoaderSimple",
                  "inputs": {"ckpt_name": "m1.safetensors"}},
            "4": {"class_type": "CLIPTextEncode",
                  "inputs": {"text": "pos", "clip": ["3", 1]}},
            "5": {"class_type": "CLIPTextEncode",
                  "inputs": {"text": "neg", "clip": ["3", 1]}},
            "6": {"class_type": "VAEEncode",
                  "inputs": {"pixels": ["2", 0], "vae": ["3", 2]}},
            "7": {"class_type": "KSampler",
                  "inputs": {"model": ["3", 0], "positive": ["4", 0],
                             "negative": ["5", 0], "latent_image": ["6", 0],
                             "seed": 0, "steps": 20, "cfg": 7.0,
                             "sampler_name": "euler", "scheduler": "simple",
                             "denoise": 0.45}},
            "8": {"class_type": "VAEDecode",
                  "inputs": {"samples": ["7", 0], "vae": ["3", 2]}},
            "9": {"class_type": "SaveImage",
                  "inputs": {"images": ["8", 0], "filename_prefix": "u"}},
        }

    def test_detect_ports(self):
        from comfy_agent.synth.ports import detect_ports
        p = detect_ports(_t2i(), _k())
        self.assertIn("IMAGE", p["outputs"])      # 7 SaveImage 上游
        p2 = detect_ports(self._upscale(), _k())
        self.assertIn(("1", "image"), p2["inputs"]["IMAGE"])

    def test_compose_id_remap_and_link(self):
        from comfy_agent.synth.compose import compose
        from comfy_agent.synth.graph import Graph
        merged = compose(_t2i(), self._upscale(), _k())
        # LoadImage 入口被删除，其下游（ImageScaleBy b2）直连 A 的 VAEDecode
        self.assertEqual(len(merged), 15)   # 7 + 9 - 1(入口删除)
        li = [n for n in merged.values() if n["class_type"] == "LoadImage"]
        self.assertEqual(li, [])
        self.assertEqual(merged["b2"]["inputs"]["image"], ["6", 0])
        g = Graph(merged, _k())
        self.assertFalse(g.has_cycle())


class TestPatterns(unittest.TestCase):
    def test_add_lora_rewiring(self):
        from comfy_agent.synth.graph import Graph
        from comfy_agent.synth.patterns import add_lora
        g = Graph(_t2i(), _k())
        new_id = add_lora(g, "l1.safetensors")
        self.assertEqual(g.api[new_id]["inputs"]["model"], ["1", 0])
        self.assertEqual(g.api["5"]["inputs"]["model"], [new_id, 0])

    def test_add_controlnet_rewiring(self):
        from comfy_agent.synth.graph import Graph
        from comfy_agent.synth.patterns import add_controlnet
        from comfy_agent.synth.ops import add_node
        g = Graph(_t2i(), _k())
        add_node(g, "90", "LoadImage", inputs={"image": "a.png"})
        ids = add_controlnet(g, "90")
        classes = [g.class_of(n) for n in ids]
        self.assertEqual(classes, ["Canny", "ControlNetLoader",
                                   "ControlNetApplyAdvanced"])
        # KSampler 的正负条件改接 ControlNetApply 的 0/1 槽
        self.assertEqual(g.api["5"]["inputs"]["positive"], [ids[2], 0])
        self.assertEqual(g.api["5"]["inputs"]["negative"], [ids[2], 1])


class TestBuilderSession(unittest.TestCase):
    def test_propose_accept_and_rollback(self):
        from comfy_agent.synth.builder import SynthSession
        s = SynthSession("测试", _k(), base_api=_t2i())
        # 合法编辑：加自包含节点（全部输入有默认值）
        r = s.propose([{"op": "add_node", "id": "10",
                        "class_type": "EmptyLatentImage"}])
        self.assertTrue(r["accepted"])
        self.assertIn("10", s.to_api())
        # 非法编辑：悬空引用 -> 回滚
        r2 = s.propose([{"op": "connect", "src": "999", "dst": "5",
                         "dst_input": "model"}])
        self.assertFalse(r2["accepted"])
        self.assertEqual(s.to_api()["5"]["inputs"]["model"], ["1", 0])  # 未污染
        self.assertEqual(s.recent_history()[-1]["accepted"], False)

    def test_undo(self):
        from comfy_agent.synth.builder import SynthSession
        s = SynthSession("测试", _k(), base_api=_t2i())
        s.propose([{"op": "add_node", "id": "10", "class_type": "Canny"}])
        s.undo()
        self.assertNotIn("10", s.to_api())


if __name__ == "__main__":
    unittest.main(verbosity=2)
