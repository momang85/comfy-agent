# -*- coding: utf-8 -*-
"""配置解析（comfy_root.local）与架构性文件输入的防误替换守卫。

来源：2026-10-02 真机跑 Qwen-Image 2.1 暴露的两个问题——
① config 不读 comfy_root.local，直接跑 CLI/MCP 时路径全指错；
② 校验据陈旧快照把 qwen 文本编码器"修复"成 minimax H3 的编码器。
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from comfy_agent import config
from comfy_agent.knowledge import Knowledge
from comfy_agent.validate import validate_workflow


class LocalRootTests(unittest.TestCase):
    def test_reads_first_nonempty_line(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "comfy_root.local"
            p.write_text("\n  D:\\pack\\ComfyUI-aki-v3.2  \n\n", encoding="utf-8")
            self.assertEqual(config._read_local_root(p), "D:\\pack\\ComfyUI-aki-v3.2")

    def test_missing_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(config._read_local_root(Path(d) / "nope.local"), "")

    def test_default_prefers_local_file(self):
        with mock.patch.object(config, "_read_local_root", return_value="X:/pack"):
            with mock.patch.object(config, "_probe_windows", return_value=""):
                self.assertEqual(config._default_comfy_root(), "X:/pack")

    def test_default_probes_when_no_local_file(self):
        with mock.patch.object(config, "_read_local_root", return_value=""):
            with mock.patch.object(config, "_probe_windows", return_value="D:/found"):
                self.assertEqual(config._default_comfy_root(), "D:/found")

    def test_normalize_accepts_pack_root(self):
        # 整合包根（其下有 ComfyUI\main.py）→ 归一到内层 ComfyUI 目录
        with tempfile.TemporaryDirectory() as d:
            pack = Path(d) / "ComfyUI-aki-v3.2"
            (pack / "ComfyUI").mkdir(parents=True)
            (pack / "ComfyUI" / "main.py").write_text("", encoding="utf-8")
            self.assertEqual(config._normalize_comfy_root(str(pack)),
                             pack / "ComfyUI")


def _knowledge_with(class_type: str, input_name: str, enum: list, models: dict):
    snap = {class_type: {"input": {"required": {input_name: [enum]},
                                   "optional": {}},
                         "output": {}}}
    return Knowledge(snap, {}, models)


class ArchInputGuardTests(unittest.TestCase):
    """架构性文件输入（unet/clip/ckpt）：找不到文件时不得给出"替代文件"建议。"""

    def test_missing_clip_gives_no_substitute(self):
        k = _knowledge_with("CLIPLoader", "clip_name",
                            ["__other_clip__.safetensors"],
                            {"text_encoders": ["__other_clip__.safetensors"]})
        wf = {"1": {"class_type": "CLIPLoader",
                    "inputs": {"clip_name": "__nope_clip__.safetensors",
                               "type": "qwen_image"}}}
        issues = validate_workflow(wf, k)
        bad = [i for i in issues if i.kind == "bad_enum"]
        self.assertEqual(len(bad), 1)
        self.assertIsNone(bad[0].suggestion)      # 不换文件，交给人工/大脑

    def test_missing_unet_gives_no_substitute(self):
        k = _knowledge_with("UNETLoader", "unet_name",
                            ["__other_unet__.safetensors"],
                            {"diffusion_models": ["__other_unet__.safetensors"]})
        wf = {"1": {"class_type": "UNETLoader",
                    "inputs": {"unet_name": "__nope_unet__.safetensors"}}}
        issues = validate_workflow(wf, k)
        bad = [i for i in issues if i.kind == "bad_enum"]
        self.assertEqual(len(bad), 1)
        self.assertIsNone(bad[0].suggestion)

    def test_vae_still_gets_same_family_suggestion(self):
        # 回归保护：VAE 丢失换同家族 VAE 是刻意保留的能力
        k = _knowledge_with("VAELoader", "vae_name",
                            ["sdxl_vae_other.safetensors"],
                            {"vae": ["sdxl_vae_other.safetensors"]})
        wf = {"1": {"class_type": "VAELoader",
                    "inputs": {"vae_name": "sdxl_vae_missing.safetensors"}}}
        issues = validate_workflow(wf, k)
        bad = [i for i in issues if i.kind == "bad_enum"]
        self.assertEqual(len(bad), 1)
        self.assertIsNotNone(bad[0].suggestion)
        self.assertEqual(bad[0].suggestion["enum"], "sdxl_vae_other.safetensors")

    def test_detect_model_type_error_gets_actionable_hint(self):
        from comfy_agent.repair import friendly_error_zh
        msg = ("ERROR: Could not detect model type of: "
               r"D:\x\models\diffusion_models\qwen_image_2.1_int8_convrot.safetensors")
        hint = friendly_error_zh({"message": msg})
        self.assertIn("ComfyUI 核心", hint)
        self.assertIn("无需重新下载", hint)


if __name__ == "__main__":
    unittest.main()
