# -*- coding: utf-8 -*-
"""Phase 2 单测：图像分析解析 / 区域级诊断 / 技能沉淀与召回（全部 mock，不花钱）。"""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class TestAnalyzeImageJSON(unittest.TestCase):
    def test_extract_structured(self):
        from brain.llm import _extract_json
        raw = ('```json\n{"content": "一只橘猫", "style": "动漫", "colors": "橙黑",'
               '"composition": "特写", "quality_issues": [], "recommended": '
               '{"template": "style_transfer", "positive_prompt": "1cat, orange fur, '
               'ghibli style", "negative_prompt": "worst quality", '
               '"params": {"denoise": 0.7, "cfg": 6}}}\n```')
        d = _extract_json(raw)
        self.assertEqual(d["content"], "一只橘猫")
        self.assertEqual(d["recommended"]["template"], "style_transfer")
        self.assertEqual(d["recommended"]["params"]["denoise"], 0.7)

    def test_analyze_calls_vlm(self):
        """analyze_image_json 走 see_image 并返回解析后 JSON。"""
        from brain.llm import VLMClient
        with mock.patch.object(VLMClient, "see_image", return_value=(
                '{"content": "宇航员", "style": "写实", "colors": "蓝",'
                '"composition": "全身", "quality_issues": [],'
                '"recommended": {"template": "i2i", "positive_prompt": "astronaut",'
                '"negative_prompt": "", "params": {"denoise": 0.5}}}')):
            v = VLMClient()
            d = v.analyze_image_json("fake.png")
            self.assertEqual(d["recommended"]["template"], "i2i")
            self.assertEqual(d["style"], "写实")


class TestRegionDiagnosis(unittest.TestCase):
    def test_string_issues_normalized(self):
        """旧格式 issues 字符串列表 -> 区域级诊断对象列表（eval 层兜底）。"""
        r = {"pass": False, "score": 3, "issues": ["橘猫未戴头盔"], "advice": "x"}
        if isinstance(r.get("issues"), list) and r["issues"] and \
                isinstance(r["issues"][0], str):
            r["issues"] = [{"location": "", "description": s, "fix_hint": ""}
                           for s in r["issues"]]
        self.assertEqual(r["issues"][0]["description"], "橘猫未戴头盔")
        self.assertIn("location", r["issues"][0])

    def test_region_schema_passthrough(self):
        """新格式（带 location/fix_hint）保持原样。"""
        r = {"issues": [{"location": "猫的头部", "description": "没头盔",
                         "fix_hint": "提示词加 astronaut helmet 并前置"}]}
        self.assertEqual(r["issues"][0]["fix_hint"],
                         "提示词加 astronaut helmet 并前置")


class TestSkillStore(unittest.TestCase):
    def test_remember_and_recall(self):
        from brain.memory import SkillStore
        store = SkillStore(Path(__file__).parent / "_skills_test.json")
        store.path.unlink(missing_ok=True)
        try:
            store.remember("画一只戴宇航头盔的橘猫，星空背景",
                           template="t2i",
                           params={"prompt": "astronaut helmet, orange cat, space"},
                           result="成功",
                           trajectory=[{"action": "fix",
                                        "note": "头盔词放提示词前部"}])
            hits = store.recall("再画一只戴宇航头盔的橘猫")
            self.assertTrue(hits, "应召回头盔橘猫技能")
            self.assertEqual(hits[0]["template"], "t2i")
            # 无关任务不召回
            self.assertEqual(store.recall("生成一段关于金融的文案"), [])
            # format_for_llm 含修复经验
            text = store.format_for_llm("画戴宇航头盔的橘猫")
            self.assertIn("头盔词放提示词前部", text)
        finally:
            store.path.unlink(missing_ok=True)

    def test_persistence_across_instances(self):
        from brain.memory import SkillStore
        p = Path(__file__).parent / "_skills_persist.json"
        p.unlink(missing_ok=True)
        try:
            SkillStore(p).remember("吉卜力风格的少女头像", template="style_transfer",
                                   result="成功")
            # 新实例（模拟重启）仍能召回
            hits = SkillStore(p).recall("帮我画吉卜力少女")
            self.assertTrue(hits)
            self.assertEqual(hits[0]["template"], "style_transfer")
        finally:
            p.unlink(missing_ok=True)

    def test_trajectory_serialization(self):
        from brain.memory import SkillStore
        p = Path(__file__).parent / "_skills_traj.json"
        p.unlink(missing_ok=True)
        try:
            store = SkillStore(p)
            store.remember("任务X", trajectory=[
                {"action": "run", "note": "第一次失败：未带头盔"},
                {"action": "fix", "note": "提示词前置 helmet"},
                {"action": "run", "note": "成功"}])
            s = store.all_skills()[0]
            self.assertEqual(len(s["trajectory"]), 3)
            self.assertEqual(s["trajectory"][1]["action"], "fix")
        finally:
            p.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
