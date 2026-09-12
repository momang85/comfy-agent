# -*- coding: utf-8 -*-
"""结构性修复单测：世界模型新鲜度 / 加载链路 / 任务契约 / 台账止损 / 失败分类。

这些用例对应项目 5 暴露的"必然产生无用消耗"的结构成因，断言的是**机制**
（不是提示词）：靠提示词的部分无法测试，能测试的部分必须锁死。
"""
import copy
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from comfy_agent import config                      # noqa: E402
from comfy_agent.world import WorldModel, file_on_disk   # noqa: E402
from brain.task import (TaskContract, AttemptLedger,      # noqa: E402
                        TaskState, strategy_signature)
from brain import policy                            # noqa: E402


class _FakeClient:
    """假 ComfyUI：只实现世界模型需要的方法。"""

    def __init__(self, snapshot=None, models=None, live=None):
        self._snapshot = snapshot or {}
        self._models = models or {}
        self._live = live or {}
        self.object_info_calls = 0
        self.models_calls = 0

    def object_info(self, node_class=None):
        self.object_info_calls += 1
        if node_class:
            return {node_class: self._live.get(node_class, {})}
        return dict(self._snapshot)

    def models(self, folder=None):
        self.models_calls += 1
        if folder:
            return list(self._models.get(folder, []))
        return {k: list(v) for k, v in self._models.items()}


class _Snap:
    """构造最小节点签名。"""

    @staticmethod
    def node(cls, inputs=None, outputs=None):
        return {cls: {"input": {"required": inputs or {}},
                      "output": outputs or ["IMAGE"]}}


class TestWorldFreshness(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="world_"))
        self._old = config.MODELS_DIR
        config.MODELS_DIR = self.tmp
        (self.tmp / "checkpoints").mkdir()
        # 快照路径也必须隔离：刷新会写盘，用真实路径会把项目的节点快照
        # 覆盖成测试用的迷你快照（曾因此让 test_phase3 全线失败）
        from comfy_agent import knowledge as kmod
        self._km = kmod
        self._old_snap = kmod.SNAPSHOT_PATH
        self._old_meta = kmod.SNAPSHOT_META_PATH
        kmod.SNAPSHOT_PATH = self.tmp / "object_info_snapshot.json"
        kmod.SNAPSHOT_META_PATH = self.tmp / "object_info_snapshot.meta.json"

    def tearDown(self):
        import shutil
        config.MODELS_DIR = self._old
        self._km.SNAPSHOT_PATH = self._old_snap
        self._km.SNAPSHOT_META_PATH = self._old_meta
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _world(self, client):
        from comfy_agent.knowledge import Knowledge
        k = Knowledge(client._snapshot, {}, client._models, client=client,
                      loaded_at=time.time())
        k._models_mtime = k._current_models_mtime()
        return WorldModel(knowledge=k, client=client)

    def test_ensure_fresh_refetches_when_ttl_expired(self):
        client = _FakeClient(snapshot=_Snap.node("A"), models={"checkpoints": []})
        w = self._world(client)
        before = client.object_info_calls
        w.knowledge.loaded_at = time.time() - config.WORLD_SNAPSHOT_TTL - 1
        w.knowledge.ensure_fresh(force=False, min_interval=0)
        self.assertGreater(client.object_info_calls, before)

    def test_models_dir_mtime_triggers_refresh(self):
        """下载落盘会改 models 目录 mtime → 清单必须重取（下完模型不能还判缺）。"""
        client = _FakeClient(snapshot=_Snap.node("A"),
                             models={"checkpoints": ["old.safetensors"]})
        w = self._world(client)
        self.assertFalse(w.model_available("new.safetensors",
                                          folders=["checkpoints"]))
        client._models = {"checkpoints": ["old.safetensors",
                                         "new.safetensors"]}
        # 模拟"刚下载完"：目录 mtime 变化
        (self.tmp / "checkpoints" / "new.safetensors").write_bytes(b"x")
        w.knowledge._models_mtime = -1
        self.assertTrue(w.model_available("new.safetensors",
                                         folders=["checkpoints"]))

    def test_disk_fallback_rescues_unknown_model(self):
        """缓存里没有、盘上有的模型不能判缺（项目 5 的"下完还说缺"）。"""
        (self.tmp / "checkpoints" / "sdXL").mkdir()
        (self.tmp / "checkpoints" / "sdXL" / "nova.safetensors").write_bytes(b"x")
        client = _FakeClient(snapshot=_Snap.node("A"), models={"checkpoints": []})
        w = self._world(client)
        self.assertTrue(w.model_available("sdXL/nova.safetensors"))
        self.assertIsNotNone(file_on_disk("sdXL/nova.safetensors"))

    def test_live_choices_prefers_server(self):
        live = {"CheckpointLoaderSimple": {"input": {"required": {
            "ckpt_name": [["fresh.safetensors"], {}]}}}}
        client = _FakeClient(snapshot=_Snap.node("A"), live=live)
        w = self._world(client)
        self.assertEqual(
            w.knowledge.live_choices("CheckpointLoaderSimple", "ckpt_name"),
            ["fresh.safetensors"])

    def test_loader_for_reports_presence(self):
        snap = {"UltralyticsDetectorProvider": {"input": {"required": {}}},
                "CheckpointLoaderSimple": {"input": {"required": {}}}}
        client = _FakeClient(snapshot=snap)
        w = self._world(client)
        ok = w.loader_for("checkpoints")
        self.assertTrue(ok["usable"])
        self.assertEqual(ok["best"], "CheckpointLoaderSimple")
        # 没有加载器的目录：usable=False 且给出说明
        miss = w.loader_for("sams")
        self.assertFalse(miss["usable"])
        self.assertIn("没有能加载", miss["note"])

    def test_route_available_distinguishes_node_vs_package(self):
        snap = {"AILab_YoloV8Adv": {"input": {"required": {}}}}
        client = _FakeClient(snapshot=snap)
        w = self._world(client)
        r = w.route_available(["UltralyticsDetectorProvider", "BboxDetectorSEGS"])
        self.assertFalse(r["available"])
        self.assertIn("UltralyticsDetectorProvider", r["missing"])
        self.assertTrue(r["hints"])

    def test_model_usability_says_download_useless_without_loader(self):
        client = _FakeClient(snapshot=_Snap.node("A"), models={"sams": []})
        w = self._world(client)
        u = w.model_usability("sam_vit_h.pth", "sams")
        self.assertFalse(u["usable"])

    def test_embeddings_need_no_loader(self):
        client = _FakeClient(snapshot=_Snap.node("A"))
        w = self._world(client)
        u = w.model_usability("easynegative.safetensors", "embeddings")
        self.assertTrue(u["usable"])
        self.assertIn("提示词", u["note"])


class TestTaskContract(unittest.TestCase):
    def test_full_body_constraint_becomes_criteria(self):
        c = TaskContract.parse("生成一个差不多风格的动漫少女全身照")
        self.assertIn("必须全身：从头到脚完整可见（含脚/鞋）", c.checks)
        self.assertIn("画风与参考图一致", c.checks)
        self.assertIn("全身", c.criteria())

    def test_criteria_falls_back_when_no_constraint(self):
        c = TaskContract.parse("随便来一张")
        self.assertEqual(c.criteria(), "画面清晰完整、主体明确、无畸形")
        self.assertEqual(c.criteria("自定义"), "自定义")

    def test_local_fix_detection(self):
        self.assertTrue(TaskContract.parse("要修手").is_local_fix)
        self.assertTrue(TaskContract.parse("把脸修一下").is_local_fix)
        self.assertFalse(TaskContract.parse("换个风格").is_local_fix)

    def test_deliverable_kind(self):
        self.assertEqual(TaskContract.parse("做个小视频").deliverable, "video")
        self.assertEqual(TaskContract.parse("画张海报").deliverable, "image")


class TestAttemptLedger(unittest.TestCase):
    def test_same_image_reroll_is_blocked_even_if_denoise_changes(self):
        """项目 5 的核心浪费：同基底只改 denoise 连着重绘。必须拦。"""
        led = AttemptLedger()
        sig = strategy_signature("i2i", {"image": "a.png", "denoise": 0.35})
        self.assertEqual(led.check(sig)["action"], "run")
        led.record(sig, {"denoise": 0.35}, {"ok": True}, score=6)
        # 第二次（改 denoise = 同签名）→ 要求换手法
        sig2 = strategy_signature("i2i", {"image": "a.png", "denoise": 0.45})
        self.assertEqual(sig, sig2)
        self.assertEqual(led.check(sig2)["action"], "must_change")
        led.record(sig2, {"denoise": 0.45}, {"ok": True}, score=4)
        # 第三次 → 直接拒绝，并给出替代做法
        v = led.check(sig)
        self.assertFalse(v["allow"])
        self.assertEqual(v["action"], "blocked")
        self.assertTrue(v["alternatives"])

    def test_different_base_image_is_a_new_strategy(self):
        led = AttemptLedger()
        a = strategy_signature("i2i", {"image": "a.png"})
        b = strategy_signature("i2i", {"image": "b.png"})
        led.record(a, {}, {"ok": True})
        self.assertEqual(led.check(b)["action"], "run")

    def test_inpaint_counts_as_different_method(self):
        led = AttemptLedger()
        glob = strategy_signature("i2i", {"image": "a.png"})
        inp = strategy_signature("inpaint", {"image": "a.png", "mask": "m.png"})
        led.record(glob, {}, {"ok": True}, score=6)
        self.assertEqual(led.check(inp)["action"], "run")
        self.assertEqual(inp[2], "inpaint")

    def test_score_regression_detected(self):
        led = AttemptLedger()
        s = strategy_signature("i2i", {"image": "a.png"})
        led.record(s, {}, {"ok": True}, score=7)
        self.assertFalse(led.regressed())
        led.record(s, {}, {"ok": True}, score=6)
        self.assertTrue(led.regressed())

    def test_unrepaired_local_fix_flag(self):
        led = AttemptLedger()
        led.record(strategy_signature("i2i", {"image": "a.png"}), {},
                   {"ok": True})
        self.assertTrue(led.unrepaired_local_fix())
        led.record(strategy_signature("inpaint", {"image": "a.png",
                                                 "mask": "m.png"}), {},
                   {"ok": True})
        self.assertFalse(led.unrepaired_local_fix())


class TestTaskStateReport(unittest.TestCase):
    def test_state_serializes_for_report(self):
        st = TaskState(TaskContract.parse("全身照"))
        st.ledger.record(strategy_signature("t2i", {"prompt": "x"}), {},
                         {"ok": True}, score=9)
        st.renders, st.wasted = 2, 1
        d = st.to_dict()
        self.assertEqual(d["renders"], 2)
        self.assertEqual(d["wasted"], 1)
        self.assertIn("全身", json.dumps(d, ensure_ascii=False))
        self.assertEqual(d["phase"], "planning")


class TestFailurePolicy(unittest.TestCase):
    def test_classification_table(self):
        cases = [
            ("LLM HTTP 400: Unknown Model", policy.CONFIG),
            ("MODEL_CAPABILITY_NOT_SUPPORTED vision", policy.CONFIG),
            ("本机没有节点 UltralyticsDetectorProvider", policy.MISSING_NODE),
            ("缺少模型: a.safetensors", policy.MISSING_ASSET),
            ("value not in choices", policy.MISSING_ASSET),
            ("LLM HTTP 502: Bad Gateway", policy.TRANSIENT),
            ("不达标 verdict=false", policy.SEMANTIC),
            ("", policy.UNKNOWN),
        ]
        for text, want in cases:
            self.assertEqual(policy.classify(text), want, text)

    def test_only_transient_may_rerender(self):
        for kind in (policy.CONFIG, policy.MISSING_ASSET, policy.MISSING_NODE,
                     policy.SEMANTIC, policy.BUDGET):
            self.assertFalse(policy.policy_for(kind)["may_rerender"], kind)
        self.assertTrue(policy.policy_for(policy.TRANSIENT)["may_rerender"])

    def test_download_helps_only_for_missing_asset(self):
        self.assertTrue(policy.policy_for(policy.MISSING_ASSET)["download_helps"])
        self.assertFalse(policy.policy_for(policy.MISSING_NODE)["download_helps"])

    def test_describe_is_actionable(self):
        self.assertIn("节点", policy.describe(policy.MISSING_NODE))
        self.assertIn("⚙", policy.describe(policy.CONFIG))


class TestEngineGuards(unittest.TestCase):
    """引擎层（不是提示词）的拦截：尺寸参数、缺模型落盘兜底、服务器枚举实时化。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="engguard_"))
        self._old = config.MODELS_DIR
        config.MODELS_DIR = self.tmp

    def tearDown(self):
        import shutil
        config.MODELS_DIR = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _k(self, checkpoints):
        from comfy_agent.knowledge import Knowledge
        from tests.test_synth import SNAPSHOT
        snap = copy.deepcopy(SNAPSHOT)
        snap["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"] = \
            [[c for c in checkpoints], {}]
        return Knowledge(snap, {}, {"checkpoints": list(checkpoints)},
                         client=_FakeClient())

    def test_size_param_blocks_before_render(self):
        """i2i 不支持 width/height：必须在执行前拦下（否则交付描述与实际不符）。"""
        from comfy_agent.runner import run_template
        r = run_template("i2i", {"image": "a.png", "prompt": "x",
                                 "width": 1024, "height": 1536},
                         knowledge=self._k(["m.safetensors"]),
                         client=object(), wait=False)
        self.assertFalse(r["ok"])
        self.assertEqual(r["stage"], "render_failed")
        self.assertIn("width", json.dumps(r["unsupported_params"]))
        self.assertIn("upscale_pass", r["hint"])

    def test_missing_template_id_is_actionable(self):
        from comfy_agent.runner import run_template
        r = run_template("", {}, knowledge=self._k([]), client=object(),
                         wait=False)
        self.assertFalse(r["ok"])
        self.assertIn("template_id", r["error"])
        self.assertEqual(r["missing_arg"], "template_id")

    def test_server_fix_uses_live_choices(self):
        from comfy_agent.runner import _apply_server_fixes
        k = self._k(["old.safetensors"])
        client = _FakeClient(live={"CheckpointLoaderSimple": {"input": {
            "required": {"ckpt_name": [["live.safetensors"], {}]}}}})
        wf = {"1": {"class_type": "CheckpointLoaderSimple",
                    "inputs": {"ckpt_name": "gone.safetensors"}}}
        fixes = _apply_server_fixes(
            wf, [{"node": "1", "input": "ckpt_name",
                  "message": "value not in choices"}], k, client)
        self.assertEqual(wf["1"]["inputs"]["ckpt_name"], "live.safetensors")
        self.assertEqual(fixes[0]["source"], "服务器实时清单")

    def test_server_fix_does_not_guess_without_choices(self):
        from comfy_agent.runner import _apply_server_fixes
        k = self._k(["old.safetensors"])
        client = _FakeClient(live={})       # 服务器没给选项
        wf = {"1": {"class_type": "UnknownNode",
                    "inputs": {"ckpt_name": "gone.safetensors"}}}
        fixes = _apply_server_fixes(
            wf, [{"node": "1", "input": "ckpt_name",
                  "message": "value not in choices"}], k, client)
        # 保留原值 + 明示未应用（绝不静默换模型）
        self.assertEqual(wf["1"]["inputs"]["ckpt_name"], "gone.safetensors")
        self.assertFalse(fixes[0]["applied"])

    def test_validate_reports_blocking(self):
        from brain.tools import tool_validate
        class Ctx:
            draft = {"1": {"class_type": "NoSuchNode", "inputs": {}}}
            knowledge = None
        ctx = Ctx()
        ctx.knowledge = self._k([])
        out = tool_validate(ctx, {})
        self.assertFalse(out["ok"])
        self.assertTrue(out["blocking"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
