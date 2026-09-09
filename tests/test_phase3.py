# -*- coding: utf-8 -*-
"""Phase 3 单测：编辑归一化 / 中间态建图 / 骨架 / 节点档案（mock LLM）/ 视频路径校验。"""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SNAPSHOT_PATH = ROOT / ".comfy-agent" / "knowledge" / "object_info_snapshot.json"


def _k():
    from comfy_agent.knowledge import Knowledge
    if SNAPSHOT_PATH.exists():
        return Knowledge(json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8")),
                         {}, {})
    # 回退轻量快照（CI 无快照时）
    from tests.test_synth import SNAPSHOT, _k as _mini
    return _mini()


class TestEditNormalization(unittest.TestCase):
    def test_toolname_key(self):
        from comfy_agent.synth.builder import _normalize_edit
        e = _normalize_edit({"add_node": {"class_type": "KSampler"}})
        self.assertEqual(e["op"], "add_node")
        self.assertEqual(e["class_type"], "KSampler")

    def test_nested_node_key(self):
        from comfy_agent.synth.builder import _normalize_edit
        e = _normalize_edit({"op": "add_node", "node": {"class_type": "Canny"}})
        self.assertEqual(e["op"], "add_node")

    def test_unknown_op_diagnostics(self):
        from comfy_agent.synth.builder import _normalize_edit
        from comfy_agent.synth import ops
        with self.assertRaises(ops.EditError) as cm:
            _normalize_edit({"frobnicate": {}})
        self.assertIn("合法操作", str(cm.exception))


class TestIntermediateBuilding(unittest.TestCase):
    def test_bare_node_accepted(self):
        from comfy_agent.synth.builder import SynthSession
        s = SynthSession("测试", _k(), base_api={})
        r = s.propose([{"op": "add_node", "class_type": "KSampler"}])
        self.assertTrue(r["accepted"])   # 连线未接=中间态，不阻断

    def test_auto_id(self):
        from comfy_agent.synth.builder import SynthSession
        s = SynthSession("测试", _k(), base_api={})
        s.propose([{"add_node": {"class_type": "KSampler"}}])
        ids = list(s.to_api().keys())
        self.assertEqual(len(ids), 1)
        self.assertTrue(ids[0].isdigit() or ids[0].startswith("100"))


class TestScaffolds(unittest.TestCase):
    def test_t2i_scaffold_valid(self):
        from comfy_agent.synth.scaffolds import scaffold
        from comfy_agent.synth.validate_graph import validate_graph
        g = scaffold(_k(), "t2i")
        blockers = [i for i in validate_graph(g)
                    if i["reason"] not in ("orphan_output", "pending_wiring")]
        self.assertEqual(blockers, [])
        self.assertEqual(len(g.nodes()), 7)

    def test_unknown_scaffold(self):
        from comfy_agent.synth.scaffolds import scaffold
        with self.assertRaises(ValueError):
            scaffold(_k(), "nope")


class TestNodeProfiles(unittest.TestCase):
    def test_learn_caches(self):
        from brain.node_profiles import learn, ProfileStore
        store = ProfileStore(Path(__file__).parent / "_profiles_test.jsonl")
        store.path.unlink(missing_ok=True)
        fake_llm = mock.Mock()
        fake_llm.chat.return_value = (
            '{"purpose": "采样", "inputs": "model接模型", "outputs": "LATENT",'
            '"wiring": "A->B", "pitfalls": []}')
        try:
            p1 = learn("KSampler", _k(), fake_llm, store)
            self.assertEqual(p1["purpose"], "采样")
            p2 = learn("KSampler", _k(), fake_llm, store)
            self.assertEqual(fake_llm.chat.call_count, 1)   # 缓存命中不再生成
            self.assertEqual(p2["purpose"], "采样")
        finally:
            store.path.unlink(missing_ok=True)

    def test_format_profile(self):
        from brain.node_profiles import format_profile
        out = format_profile({"class": "X", "purpose": "做什么",
                              "inputs": "a接b", "pitfalls": ["坑1"]})
        self.assertIn("做什么", out)
        self.assertIn("坑1", out)


class TestVideoSampling(unittest.TestCase):
    def test_reject_bad_path(self):
        from brain.eval.video import sample_frames, VideoError
        with self.assertRaises(VideoError):
            sample_frames("不存在.mp4")

    def test_reject_bad_ext(self):
        from brain.eval.video import sample_frames, VideoError
        with self.assertRaises(VideoError):
            sample_frames(__file__)   # .py 不是视频


if __name__ == "__main__":
    unittest.main(verbosity=2)
