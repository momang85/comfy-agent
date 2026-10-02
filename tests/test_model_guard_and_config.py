# -*- coding: utf-8 -*-
"""配置解析（comfy_root.local）与架构性文件输入的防误替换守卫。

来源：2026-10-02 真机跑 Qwen-Image 2.1 暴露的两个问题——
① config 不读 comfy_root.local，直接跑 CLI/MCP 时路径全指错；
② 校验据陈旧快照把 qwen 文本编码器"修复"成 minimax H3 的编码器。
"""
import json
import os
import tempfile
import time
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


class EnsureFreshTests(unittest.TestCase):
    """校验前必须按 TTL 对齐服务器现状（防止拿过期快照判"不存在"）。"""

    class _K:
        def __init__(self, calls, boom=False):
            self.calls = calls
            self.boom = boom
            self.snapshot = {}
            self.models = {}

        def ensure_fresh(self, *a, **k):
            self.calls.append(1)
            if self.boom:
                raise RuntimeError("server down")

        def node_info(self, cls):
            return None

        def find_nodes(self, cls, limit=3):
            return []

    class _C:
        def is_alive(self):
            return False

    def test_ensure_fresh_called_before_validation(self):
        from comfy_agent.runner import run_workflow
        calls = []
        r = run_workflow({"1": {"class_type": "NoSuchNode", "inputs": {}}},
                         client=self._C(), knowledge=self._K(calls))
        self.assertTrue(calls, "校验前没有刷新节点/模型快照")
        self.assertFalse(r["ok"])
        self.assertEqual(r["stage"], "validation_failed")

    def test_ensure_fresh_failure_is_tolerated(self):
        from comfy_agent.runner import run_workflow
        r = run_workflow({"1": {"class_type": "NoSuchNode", "inputs": {}}},
                         client=self._C(), knowledge=self._K([], boom=True))
        self.assertEqual(r["stage"], "validation_failed")   # 刷新失败不阻断诊断


class SnapshotFreshnessTests(unittest.TestCase):
    """快照的"新鲜度"必须按真实抓取时间算，否则 TTL 永不生效。"""

    def _wire(self, d: Path, fetched_at: float):
        snap = d / "snap.json"
        meta = d / "snap.meta.json"
        snap.write_text(json.dumps({"SomeNode": {"input": {"required": {}}}}),
                        encoding="utf-8")
        meta.write_text(json.dumps({"fetched_at": fetched_at}), encoding="utf-8")
        return snap, meta

    def test_snapshot_fetched_at_reads_meta(self):
        import comfy_agent.knowledge as K
        with tempfile.TemporaryDirectory() as d:
            snap, meta = self._wire(Path(d), 1700000000.0)
            with mock.patch.object(K, "SNAPSHOT_PATH", snap), \
                 mock.patch.object(K, "SNAPSHOT_META_PATH", meta):
                self.assertEqual(K.Knowledge._snapshot_fetched_at(), 1700000000.0)

    def test_build_uses_real_fetch_time_and_refreshes_after_ttl(self):
        import comfy_agent.knowledge as K
        calls = []

        class FakeClient:
            def object_info(self):
                calls.append("object_info")
                return {"NewNode": {"input": {"required": {}}}}

            def models(self):
                return {}

        with tempfile.TemporaryDirectory() as d:
            snap, meta = self._wire(Path(d), time.time() - 3600)   # 一小时前的快照
            with mock.patch.object(K, "SNAPSHOT_PATH", snap), \
                 mock.patch.object(K, "SNAPSHOT_META_PATH", meta), \
                 mock.patch.object(K.Knowledge, "_load_manager_extmap", return_value={}):
                k = K.Knowledge.build(client=FakeClient())
                self.assertLess(k.loaded_at, time.time() - 600)   # 不是"刚装载"
                self.assertEqual(calls, [])                       # build 本身不重取
                k.ensure_fresh()
                self.assertIn("object_info", calls)               # TTL 过期后确实重取

    def test_build_fresh_snapshot_does_not_refetch(self):
        import comfy_agent.knowledge as K
        calls = []

        class FakeClient:
            def object_info(self):
                calls.append("object_info")
                return {}

            def models(self):
                return {}

        with tempfile.TemporaryDirectory() as d:
            snap, meta = self._wire(Path(d), time.time())        # 刚抓的
            with mock.patch.object(K, "SNAPSHOT_PATH", snap), \
                 mock.patch.object(K, "SNAPSHOT_META_PATH", meta), \
                 mock.patch.object(K.Knowledge, "_load_manager_extmap", return_value={}):
                k = K.Knowledge.build(client=FakeClient())
                k.ensure_fresh()
                self.assertEqual(calls, [])                      # 未过期不重取


class NodeRenameThresholdTests(unittest.TestCase):
    """节点改名建议要有相似度门槛（曾把 QwenImage21Cache 改成 WanVideoMagCache）。"""

    def test_unrelated_name_gets_no_rename(self):
        from comfy_agent.validate import _closest_node_name
        self.assertIsNone(_closest_node_name("QwenImage21Cache", ["WanVideoMagCache"]))

    def test_close_name_gets_rename(self):
        from comfy_agent.validate import _closest_node_name
        self.assertEqual(_closest_node_name("CheckpointLoader", ["CheckpointLoaderSimple"]),
                         "CheckpointLoaderSimple")

    def test_validate_reports_missing_without_bogus_rename(self):
        class K:
            snapshot = {}
            models = {}

            def node_info(self, cls):
                return None

            def find_nodes(self, cls, limit=3):
                return [{"class": "WanVideoMagCache"}]

        issues = validate_workflow({"2": {"class_type": "QwenImage21Cache",
                                          "inputs": {}}}, K())
        missing = [i for i in issues if i.kind == "missing_node"]
        self.assertEqual(len(missing), 1)
        self.assertIsNone(missing[0].suggestion)


class AutogrowInputTests(unittest.TestCase):
    """动态输入组（Autogrow）：官方 Qwen 2.1 节点是 names + min=0。"""

    def _k(self, min_=0):
        snap = {"TextEncodeQwenImage21": {
            "input": {"required": {
                "clip": ["CLIP"],
                "prompt": ["STRING", {"multiline": True}],
                "images": ["COMFY_AUTOGROW_V3",
                           {"template": {
                               "input": {"required": {"image": ["IMAGE", {}]}},
                               "names": ["image_1", "image_2"],
                               "min": min_}}],
            }, "optional": {}},
            "output": {}}}
        return Knowledge(snap, {}, {})

    def _issues(self, wf, min_=0):
        return [i for i in validate_workflow(wf, self._k(min_))
                if i.input_name == "images"]

    def test_empty_dict_is_accepted(self):
        wf = {"4": {"class_type": "TextEncodeQwenImage21",
                    "inputs": {"clip": ["3", 0], "prompt": "a cat", "images": {}}}}
        self.assertEqual(self._issues(wf), [])

    def test_min_zero_allows_absent_group(self):
        # 官方模板 min=0：整组不接也合法（此前被误判为缺）
        wf = {"4": {"class_type": "TextEncodeQwenImage21",
                    "inputs": {"clip": ["3", 0], "prompt": "a cat"}}}
        self.assertEqual(self._issues(wf), [])

    def test_min_positive_reports_absent_group(self):
        wf = {"4": {"class_type": "TextEncodeQwenImage21",
                    "inputs": {"clip": ["3", 0], "prompt": "a cat"}}}
        self.assertEqual(len(self._issues(wf, min_=2)), 1)

    def test_named_form_accepted(self):
        wf = {"4": {"class_type": "TextEncodeQwenImage21",
                    "inputs": {"clip": ["3", 0], "prompt": "a cat",
                               "images.image_1": ["1", 0]}}}
        self.assertEqual(self._issues(wf), [])

    def test_legacy_numbered_form_accepted(self):
        wf = {"4": {"class_type": "TextEncodeQwenImage21",
                    "inputs": {"clip": ["3", 0], "prompt": "a cat",
                               "images0": ["1", 0]}}}
        self.assertEqual(self._issues(wf), [])


if __name__ == "__main__":
    unittest.main()
