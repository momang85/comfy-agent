"""comfy_status 工具：把 /system_stats 变成「我这卡能跑吗」的答案。

背景：用户问「本机什么显卡/多少显存/ComfyUI 什么版本」时，模型只能反复翻
list_models（那里没有版本和显存），最后回答「工具里查不到」。这个工具补上
最后一块拼图，并顺带按模板的显存预估分三档回答能不能跑。
"""

import unittest
from types import SimpleNamespace

from brain.tools import TOOLS, tool_comfy_status, tools_schema_for_llm

GB = 10 ** 9          # 十进制 GB，与显卡标称口径一致（12GB 卡 = 12.79e9 字节）


def _stats(vram_gb=12.8, free_gb=1.8, version="0.38.0"):
    return {
        "system": {
            "os": "win32",
            "ram_total": 33.5 * GB,
            "comfyui_version": version,
            "python_version": "3.13.11 (tags/v3.13.11:6278944) [MSC v.1944 64 bit]",
            "pytorch_version": "2.13.0+cu130",
        },
        "devices": [{
            # 真实 payload 里带后端后缀，回答用户时要截掉
            "name": "cuda:0 NVIDIA GeForce RTX 5070 Ti Laptop GPU : cudaMallocAsync",
            "type": "cuda",
            "index": 0,
            "vram_total": int(vram_gb * GB),
            "vram_free": int(free_gb * GB),
        }],
    }


class _Knowledge:
    """最小世界模型替身：只有 model_exists。"""

    def __init__(self, have=()):
        self.have = {h.lower() for h in have}

    def model_exists(self, name, folders=None):
        return str(name).lower() in self.have


class _Ctx:
    def __init__(self, stats=None, exc=None, knowledge=None):
        class _Client:
            def system_stats(_self):
                if exc is not None:
                    raise exc
                return stats

        self.client = _Client()
        self.knowledge = knowledge

    def _row(self, r, tid):
        for row in r["templates"]:
            if row["id"] == tid:
                return row
        raise AssertionError(f"模板列表里没有 {tid}")


class TestComfyStatus(unittest.TestCase):

    def test_version_and_gpu(self):
        r = tool_comfy_status(_Ctx(_stats()), {})
        self.assertTrue(r["ok"])
        self.assertEqual(r["comfyui_version"], "0.38.0")
        self.assertEqual(r["pytorch"], "2.13.0+cu130")
        self.assertEqual(r["python"], "3.13.11")
        self.assertEqual(r["ram_total_gb"], 33.5)
        self.assertEqual(len(r["devices"]), 1)
        dev = r["devices"][0]
        self.assertEqual(dev["name"], "cuda:0 NVIDIA GeForce RTX 5070 Ti Laptop GPU")
        self.assertEqual(dev["vram_total_gb"], 12.8)
        self.assertEqual(dev["vram_free_gb"], 1.8)
        self.assertEqual(r["vram_total_gb"], 12.8)

    def test_template_buckets_on_small_card(self):
        """12GB 标称卡（12.8e9 字节）上，qwen_edit（预估 12GB）是紧张而不是「随便跑」。"""
        r = tool_comfy_status(_Ctx(_stats(vram_gb=12.8)), {})
        self.assertEqual(_Ctx(_stats())._row(r, "qwen_edit")["fit"], "tight")

    def test_template_buckets_on_big_card(self):
        r = tool_comfy_status(_Ctx(_stats(vram_gb=24.0, free_gb=20.0)), {})
        self.assertEqual(_Ctx(_stats())._row(r, "qwen_edit")["fit"], "can_run")

    def test_template_buckets_on_tiny_card(self):
        r = tool_comfy_status(_Ctx(_stats(vram_gb=6.0, free_gb=4.0)), {})
        self.assertEqual(_Ctx(_stats())._row(r, "qwen_edit")["fit"], "too_big")

    def test_missing_models_are_reported(self):
        """显存够、模型缺 → ready 必须为 false，不能算「此刻真能跑」。"""
        have_qwen = ["qwen_image_2.1_int8_convrot.safetensors",
                     "qwen3vl_8b_int8_convrot.safetensors",
                     "qwen_image_2.1_vae_bf16.safetensors"]
        r = tool_comfy_status(
            _Ctx(_stats(), knowledge=_Knowledge(have_qwen)), {})
        row = _Ctx(_stats())._row(r, "qwen_edit")
        self.assertEqual(row["missing_models"], [])
        self.assertTrue(row["ready"])
        self.assertIn("qwen_edit", r["runnable_now"])
        # t2i 走 checkpoint，本机没有 → 显存虽够但要报缺
        t2i = _Ctx(_stats())._row(r, "t2i")
        self.assertTrue(t2i["missing_models"])
        self.assertFalse(t2i["ready"])
        self.assertNotIn("t2i", r["runnable_now"])

    def test_summary_names_ready_templates(self):
        """summary 是第一行结论——模型最容易漏掉「其实有能跑的那个」。"""
        have_qwen = ["qwen_image_2.1_int8_convrot.safetensors",
                     "qwen3vl_8b_int8_convrot.safetensors",
                     "qwen_image_2.1_vae_bf16.safetensors"]
        r = tool_comfy_status(_Ctx(_stats(), knowledge=_Knowledge(have_qwen)), {})
        self.assertIn("qwen_edit", r["summary"])
        # 缺模型的不许出现在「能直接跑」里
        bare = tool_comfy_status(_Ctx(_stats(), knowledge=_Knowledge()), {})
        self.assertNotIn("qwen_edit", bare["summary"])
        self.assertTrue(bare["summary"].startswith("现在能直接跑"))

    def test_missing_uses_exact_match(self):
        """模糊匹配会把别的模型当成同款，这里必须严格。"""
        r = tool_comfy_status(_Ctx(_stats(), knowledge=_Knowledge(["qwen_edit"])), {})
        row = _Ctx(_stats())._row(r, "qwen_edit")
        self.assertEqual(len(row["missing_models"]), 3)
        self.assertFalse(row["ready"])

    def test_unreachable_is_honest(self):
        r = tool_comfy_status(_Ctx(exc=OSError("connection refused")), {})
        self.assertFalse(r["ok"])
        self.assertIn("连不上 ComfyUI", r["error"])
        self.assertIn("connection refused", r["error"])

    def test_registered_and_described(self):
        self.assertIn("comfy_status", TOOLS)
        self.assertEqual(TOOLS["comfy_status"]["args"], {})
        schema = tools_schema_for_llm()
        self.assertIn("comfy_status", schema)
        # 描述里要点明「能跑吗」，否则模型想不起来先问它
        self.assertIn("能跑吗", schema)


if __name__ == "__main__":
    unittest.main()
