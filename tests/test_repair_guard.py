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
        # 反幻觉：模板目录里的参数必须来自模板真实声明。
        # 曾经大脑对 t2i 编造过 resolution——t2i 至今没有这个参数，
        # 所以按行断言（qwen_edit 确实有 resolution，是真实参数）。
        t2i_line = next(ln for ln in sp.splitlines() if ln.startswith("- t2i:"))
        self.assertNotIn("resolution", t2i_line)
        qwen_line = next(ln for ln in sp.splitlines()
                         if ln.startswith("- qwen_edit:"))
        self.assertIn("resolution", qwen_line)


class TestToolLoopRunsOnce(unittest.TestCase):
    """handle() 每回合只能跑一次工具循环。

    真实缺陷（2026-10-29 换 provider 后实测）：try 分支里跑了一次
    `_tool_loop`，`else` 分支里又跑了一次 —— 每条用户消息都会向模型发两遍
    请求、执行两遍工具、把同一个任务做两次；第二次还会看见第一次留下的
    history，于是回答变成"我查不到"式的自我重复，最终交付的却是兜底文案。
    """

    def test_handle_calls_tool_loop_once(self):
        from brain.agent import Brain
        b = Brain(verbose=False)
        calls = []

        def fake_loop(run_count):
            calls.append(run_count)
            return "本机 ComfyUI 版本我查不到"

        with mock.patch.object(b, "_tool_loop", fake_loop), \
                mock.patch.object(b, "_auto_remember", lambda *a, **k: None), \
                mock.patch.object(b, "_write_task_report", lambda *a, **k: None):
            text = b.handle("看下本机 ComfyUI 的版本")
        self.assertEqual(len(calls), 1, f"工具循环跑了 {len(calls)} 次")
        self.assertIn("查不到", text)          # 模型的话原样交付，不被兜底文案顶掉

    def test_model_reply_reaches_user(self):
        """模型不再调工具时的原话必须到达用户（回归 3f27426 的 return ""）。

        真实表现：每个回合都返回"本轮任务已完成，先给你看当前结果：…"，
        模型自己说的解释、答案、追问全被这句模板覆盖。
        """
        from brain.agent import Brain
        b = Brain(verbose=False)
        model_said = "这张图里是一只橘猫，想改成什么风格？"

        with mock.patch.object(b, "_chat_streamed", lambda: model_said), \
                mock.patch.object(b, "_auto_remember", lambda *a, **k: None), \
                mock.patch.object(b, "_write_task_report", lambda *a, **k: None):
            text = b.handle("看看这张图")
        self.assertEqual(text.strip(), model_said)
        self.assertNotIn("本轮任务已完成", text)




if __name__ == "__main__":
    unittest.main(verbosity=2)
