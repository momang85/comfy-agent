# -*- coding: utf-8 -*-
"""项目 6 事故的回归：旧代码静默运行 / 输入图没进 /input / 技术失败吃掉额度。

事故链（2026-09-13 10:36，proj_e7f6c011）：
  1. Web UI 进程 09:58 启动，而 `local_repair` 的输入文件声明是 10:08 之后加的
     → 服务仍用旧代码 → 引擎认为"没有输入文件要上传" → 图片路径原样进工作流
  2. ComfyUI `LoadImage.VALIDATE_INPUTS` → `exists_annotated_filepath` → 不在 /input
     → `Invalid image file` → `repair_failed`（两次：裸名一次、绝对路径一次）
  3. 这两次"根本没跑起来"被台账当成"local_repair 试过 1 次"，于是后续同一手法
     被 `blocked` 拒绝两次 → 用户看到"工具反复失败"

本文件锁住四个修复：
  ① 代码指纹：改完没重启 → 显式陈旧告警 + 拒绝生成类工具
  ② 裸文件名解析搜 outputs/（跨轮产物），找不到给可执行报错
  ③ 技术失败不消耗同手法额度（tech_failures 单算）
  ④ bad_input 归类 + 允许"修好路径后重试同一步"
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from comfy_agent import config                                  # noqa: E402
from comfy_agent.freshness import (changed_since, code_fingerprint,   # noqa: E402
                                  is_stale, stale_message)
from brain.task import (AttemptLedger, TaskContract, TaskState,   # noqa: E402
                        strategy_signature)
from brain import policy                                         # noqa: E402


class TestCodeFreshness(unittest.TestCase):
    def test_fingerprint_covers_decision_paths(self):
        fp = code_fingerprint()
        for rel in ("comfy_agent/runner.py", "brain/agent.py",
                    "comfy_agent/templates/base.py"):
            self.assertIn(rel, fp)
            self.assertNotEqual(fp[rel], "missing")

    def test_unchanged_is_not_stale(self):
        self.assertFalse(is_stale(code_fingerprint()))
        self.assertEqual(changed_since(code_fingerprint()), [])

    def test_changed_file_detected(self):
        snap = code_fingerprint()
        snap["comfy_agent/templates/base.py"] = "0:0"     # 模拟"磁盘上被改过"
        changed = changed_since(snap)
        self.assertEqual(changed, ["comfy_agent/templates/base.py"])
        self.assertTrue(is_stale(snap))
        msg = stale_message(changed)
        self.assertIn("重启", msg)
        self.assertIn("base.py", msg)

    def test_missing_file_counts_as_changed(self):
        snap = code_fingerprint()
        snap["comfy_agent/world.py"] = "missing"
        self.assertIn("comfy_agent/world.py", changed_since(snap))

    def test_stale_blocks_generation_tools(self):
        """陈旧代码下必须拒绝生成类工具（不白烧 GPU、不用旧逻辑出图）。"""
        from brain.agent import Brain
        import inspect
        src = inspect.getsource(Brain._tool_loop)
        self.assertIn("stale_code", src)
        self.assertIn("run_template", src)


class TestInputResolution(unittest.TestCase):
    """裸文件名：搜 outputs（跨轮产物）；找不到给可执行报错，不再丢给 LoadImage。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="p6_input_"))
        self.proj = self.tmp / "projects" / "p"
        (self.proj / "outputs" / "template_t2i_20260913_103440").mkdir(parents=True)
        (self.proj / "uploads").mkdir(parents=True)
        self.artifact = (self.proj / "outputs" / "template_t2i_20260913_103440"
                         / "agent_t2i_00062_.png")
        self.artifact.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 64)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_bare_artifact_name_resolves_from_outputs(self):
        from comfy_agent.runner import _ensure_inputs_uploaded
        from comfy_agent.templates import get_template
        seen = []
        class Cli:
            def upload_image(self, p):
                seen.append(str(p)); return {"name": Path(p).name}
            def get(self, path):
                return {"LoadImage": {"input": {"required": {"image": [[]]}}}}
        tpl = get_template("i2i")
        params, notes, err = _ensure_inputs_uploaded(
            tpl, {"image": "agent_t2i_00062_.png"}, Cli(),
            str(self.proj / "outputs"))
        self.assertIsNone(err, err)
        self.assertEqual(params["image"], "agent_t2i_00062_.png")
        self.assertEqual(seen, [str(self.artifact)])      # 真的上传了那一张
        self.assertTrue(any("自动上传" in n for n in notes))

    def test_unknown_name_gives_actionable_error(self):
        from comfy_agent.runner import _ensure_inputs_uploaded
        from comfy_agent.templates import get_template
        class Cli:
            def upload_image(self, p):
                return {"name": Path(p).name}
            def get(self, path):        # 服务器 /input 里也没有
                return {"LoadImage": {"input": {"required": {"image": [["other.png"], {}]}}}}
        tpl = get_template("i2i")
        params, notes, err = _ensure_inputs_uploaded(
            tpl, {"image": "no_such_file.png"}, Cli(), str(self.proj / "outputs"))
        self.assertIsNotNone(err)
        self.assertIn("upload_image", err)          # 给出可执行的下一步
        self.assertIn("绝对路径", err)

    def test_server_side_name_is_left_alone(self):
        """已经在 /input 的服务器文件名不该被误报。"""
        from comfy_agent.runner import _ensure_inputs_uploaded
        from comfy_agent.templates import get_template
        class Cli:
            def upload_image(self, p):
                return {"name": Path(p).name}
            def get(self, path):
                return {"LoadImage": {"input": {"required": {
                    "image": [["already_uploaded.png"], {}]}}}}
        tpl = get_template("i2i")
        params, notes, err = _ensure_inputs_uploaded(
            tpl, {"image": "already_uploaded.png"}, Cli(), str(self.proj / "outputs"))
        self.assertIsNone(err)
        self.assertEqual(params["image"], "already_uploaded.png")

    def _ctx(self):
        base = self.proj          # 闭包绑定：类体内的 self 不是外层 self

        class P:
            def uploads_dir(self): return base / "uploads"
            def outputs_dir(self): return base / "outputs"

        return type("C", (), {"project": P()})()

    def test_analyze_image_resolves_output_artifact(self):
        from brain.tools import _resolve_upload
        hit = _resolve_upload(self._ctx(), "agent_t2i_00062_.png")
        self.assertIsNotNone(hit)
        self.assertEqual(Path(hit).name, "agent_t2i_00062_.png")

    def test_fuzzy_name_still_not_substituted(self):
        """精确匹配才有资格兜底——模糊名不得指向别的文件（老缺陷的护栏）。"""
        from brain.tools import _resolve_upload
        self.assertIsNone(_resolve_upload(self._ctx(), "agent_t2i_00062.png"))


class TestTechnicalFailures(unittest.TestCase):
    """技术失败（没跑起来）不得消耗同手法额度——项目 6 的核心加剧因素。"""

    def _sig(self):
        return strategy_signature("local_repair", {"image": "a.png",
                                                  "target": "face"})

    def test_render_failed_is_technical(self):
        for stage in ("render_failed", "validation_failed", "repair_failed",
                      "execution_failed"):
            self.assertTrue(AttemptLedger.is_technical({"stage": stage}), stage)
        self.assertTrue(AttemptLedger.is_technical(
            {"stage": "execution_failed", "exec_error": {"message": "x"}}))

    def test_completed_is_not_technical(self):
        self.assertFalse(AttemptLedger.is_technical(
            {"stage": "completed", "ok": True}))

    def test_two_technical_failures_still_allow_same_strategy(self):
        led = AttemptLedger()
        sig = self._sig()
        for _ in range(2):
            led.record(sig, {}, {"stage": "repair_failed", "ok": False},
                       technical=True)
        self.assertEqual(led.tech_failures, 2)
        self.assertTrue(led.check(sig)["allow"], "技术失败不该锁死手法")
        self.assertEqual(led.signature_counts.get(sig, 0), 0)

    def test_semantic_failures_still_rationed(self):
        """真跑起来但没达标 → 仍然第 2 次要求换手法、第 3 次拒绝。"""
        led = AttemptLedger()
        sig = self._sig()
        led.record(sig, {}, {"stage": "completed", "ok": True}, score=6)
        self.assertEqual(led.check(sig)["action"], "must_change")
        self.assertEqual(led.check(sig)["action"], "blocked")

    def test_true_story_sequence_from_project6(self):
        """复刻项目 6：两次技术失败 → 第三次**允许**跑（当时被拒了两次）。"""
        led = AttemptLedger()
        sig = self._sig()
        led.record(sig, {}, {"stage": "repair_failed", "ok": False},
                   technical=True, reason="Invalid image file")
        led.record(sig, {}, {"stage": "repair_failed", "ok": False},
                   technical=True, reason="Invalid image file")
        self.assertTrue(led.check(sig)["allow"])
        # 跑起来但没修好 → 才开始省额度
        led.record(sig, {}, {"stage": "completed", "ok": True}, score=5)
        self.assertEqual(led.check(sig)["action"], "must_change")

    def test_report_includes_tech_failures(self):
        st = TaskState(TaskContract.parse("修手"))
        st.ledger.record(self._sig(), {}, {"stage": "repair_failed"},
                         technical=True)
        self.assertEqual(st.to_dict()["ledger"]["tech_failures"], 1)


class TestEmptyMaskNotCountedAsRepair(unittest.TestCase):
    """遮罩为空 = 什么都没重绘：不算"修过但没修好"，也不许当修复成功交付。"""

    def test_mask_invalid_is_treated_as_technical(self):
        from brain.agent import Brain
        import inspect
        src = inspect.getsource(Brain._tool_loop)
        self.assertIn("mask_invalid", src)
        self.assertIn("technical=_AL.is_technical(result) or mask_bad", src)

    def test_empty_mask_note_recorded_in_report(self):
        from brain.task import TaskState, TaskContract
        st = TaskState(TaskContract.parse("修脸"))
        st.notes.append("遮罩无效：没有检测到要修的目标")
        self.assertIn("遮罩无效", st.to_dict()["notes"][0])


class TestBadInputPolicy(unittest.TestCase):
    def test_classification(self):
        self.assertEqual(policy.classify("Invalid image file: x.png"),
                         policy.BAD_INPUT)
        self.assertEqual(policy.classify("图片不存在: a.png"), policy.BAD_INPUT)
        self.assertEqual(
            policy.classify("image='x' 既不是本机文件，也不在 ComfyUI /input"),
            policy.BAD_INPUT)

    def test_allows_retry_after_fixing_path(self):
        p = policy.policy_for(policy.BAD_INPUT)
        self.assertTrue(p["may_rerender"])          # 修好路径重试同一步是对的
        self.assertIn("fix_input_path", p["actions"])
        self.assertFalse(p["download_helps"])       # 不是缺模型，别去下载

    def test_describe_is_actionable(self):
        msg = policy.describe(policy.BAD_INPUT)
        self.assertIn("upload_image", msg)
        self.assertIn("绝对路径", msg)

    def test_bad_input_does_not_shadow_other_classes(self):
        """顺序敏感：缺模型/缺节点/配置错不能被 bad_input 抢走。"""
        self.assertEqual(policy.classify("缺少模型: a.safetensors"),
                         policy.MISSING_ASSET)
        self.assertEqual(policy.classify("本机没有节点 X"), policy.MISSING_NODE)
        self.assertEqual(policy.classify("LLM HTTP 400: Unknown Model"),
                         policy.CONFIG)


if __name__ == "__main__":
    unittest.main(verbosity=2)
