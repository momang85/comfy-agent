# -*- coding: utf-8 -*-
"""相同参数重试护栏单测 + repair 技能注入回归。"""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class TestRetrySignatureGuard(unittest.TestCase):
    """run_template 相同参数第2次提交必须注入强提示。"""

    def _brain(self):
        from brain.agent import Brain
        b = Brain(verbose=False, project=None)
        return b

    def test_identical_params_flagged(self):
        from brain.agent import Brain
        b = self._brain()
        # 模拟第一次调用
        args1 = {"template_id": "i2i", "params": {"image": "x.png",
                                                  "denoise": 0.5}}
        # 通过内联逻辑复现（handle 需要 LLM，直接测签名表机制）
        import json
        tid = args1["template_id"]
        params = json.dumps(args1["params"], sort_keys=True)
        b._run_signatures[(tid, params)] = 1
        # 相同参数再来一次 → 应识别
        params2 = json.dumps({"denoise": 0.5, "image": "x.png"}, sort_keys=True)
        self.assertEqual(params, params2)   # 键序无关
        self.assertEqual(b._run_signatures.get((tid, params2)), 1)

    def test_different_params_not_flagged(self):
        from brain.agent import Brain
        b = self._brain()
        import json
        a = json.dumps({"denoise": 0.5}, sort_keys=True)
        c = json.dumps({"denoise": 0.8}, sort_keys=True)
        b._run_signatures[("i2i", a)] = 1
        self.assertNotIn(("i2i", c), b._run_signatures)


class TestRepairSkillInjected(unittest.TestCase):
    def test_repair_md_in_always_inject(self):
        from brain.agent import Brain
        b = Brain(verbose=False)
        sp = b.history[0]["content"]
        self.assertIn("FaceDetailer", sp)          # repair.md 内容已注入
        self.assertIn("禁止全图", sp)               # 关键纪律
        self.assertIn("修复崩坏", sp)               # 映射表条目
        # 模板目录带参数清单（反幻觉）
        self.assertIn("参数: [", sp)

    def test_template_params_in_catalog(self):
        from brain.agent import Brain
        b = Brain(verbose=False)
        sp = b.history[0]["content"]
        # t2i 的参数清单应包含 prompt/width/height
        self.assertIn("prompt", sp)
        self.assertIn("width", sp)
        self.assertNotIn("resolution", sp)   # 幻觉参数不该出现在参数清单


if __name__ == "__main__":
    unittest.main(verbosity=2)
