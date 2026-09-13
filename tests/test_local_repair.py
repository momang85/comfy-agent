# -*- coding: utf-8 -*-
"""局部修复（自动遮罩）单测：模板四条路线、遮罩护栏、自动路由触发条件。

背景：局部问题此前只能整图重绘（项目 5 实测 6→4→6→6 纯烧 GPU）。
这里锁住"自动改走 local_repair"的机制与三条保命护栏：
  ① 遮罩绝不能从本轮上传自动填充（否则原图当遮罩 = 整图重绘）
  ② 遮罩几乎为空（没检测到目标）→ 判无效，不许假装修好
  ③ 触发条件五条全满足才自动，缺一条就交回大脑/问用户
"""
import copy
import json
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from comfy_agent import config                       # noqa: E402
from comfy_agent.mask import check_mask, mask_coverage, bbox   # noqa: E402
from comfy_agent.templates import get_template       # noqa: E402
from comfy_agent.templates.image import LocalRepair  # noqa: E402
from brain.task import (AttemptLedger, TaskContract, TaskState,   # noqa: E402
                        strategy_signature)


def make_png(path: Path, w: int, h: int, white_frac_x: float) -> None:
    """生成白块在左侧的 PNG（8-bit RGB），用于遮罩覆盖率测试。"""
    raw = b""
    for _y in range(h):
        row = bytearray([0])
        for x in range(w):
            row += bytes((255, 255, 255) if x < int(w * white_frac_x)
                         else (0, 0, 0))
        raw += bytes(row)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))

    path.write_bytes(b"\x89PNG\r\n\x1a\n"
                     + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw, 9))
                     + chunk(b"IEND", b""))


class TestLocalRepairTemplate(unittest.TestCase):
    def setUp(self):
        self.tpl = get_template("local_repair")
        self.assertIsNotNone(self.tpl, "local_repair 模板未注册")

    def test_four_routes_render_expected_nodes(self):
        cases = {
            "hand": ["AILab_YoloV8Adv", "GrowMask", "VAEEncodeForInpaint"],
            "face": ["DWPreprocessor", "FaceMaskFromPoseKeypoints", "GrowMask",
                     "VAEEncodeForInpaint"],
            "box": ["MaskRectAreaAdvanced", "VAEEncodeForInpaint"],
            "provided": ["LoadImage", "VAEEncodeForInpaint"],
        }
        for target, musts in cases.items():
            params = {"image": "a.png", "prompt": "perfect hands",
                      "target": target}
            if target == "provided":
                params["mask"] = "m.png"
            if target == "box":
                params["box"] = "0.2,0.3,0.4,0.4"
            classes = [v["class_type"] for v in self.tpl.render(params).values()]
            for m in musts:
                self.assertIn(m, classes, f"{target} 缺节点 {m}")
            # 每条路线都必须"只把遮罩区贴回原图"
            self.assertIn("ImageCompositeMasked", classes)
            self.assertIn("MaskToImage", classes)   # 存遮罩供引擎判定

    def test_composite_destination_is_original_image(self):
        wf = self.tpl.render({"image": "a.png", "target": "hand",
                              "prompt": "x"})
        comp = next(v for v in wf.values()
                    if v["class_type"] == "ImageCompositeMasked")
        self.assertEqual(comp["inputs"]["destination"], ["1", 0])
        self.assertEqual(comp["inputs"]["x"], 0)
        self.assertEqual(comp["inputs"]["y"], 0)

    def test_face_route_pins_local_weights(self):
        """DWPose 默认值是盘上不存在的 .onnx；必须钉住盘上的 .torchscript.pt。"""
        wf = self.tpl.render({"image": "a.png", "target": "face",
                              "prompt": "x"})
        dw = next(v for v in wf.values()
                  if v["class_type"] == "DWPreprocessor")
        self.assertEqual(dw["inputs"]["bbox_detector"],
                         LocalRepair.FACE_BBOX)
        self.assertEqual(dw["inputs"]["pose_estimator"],
                         LocalRepair.FACE_POSE)
        self.assertTrue(LocalRepair.FACE_BBOX.endswith(".torchscript.pt"))
        self.assertTrue(LocalRepair.FACE_POSE.endswith(".torchscript.pt"))

    def test_auto_infers_target(self):
        self.assertEqual(self.tpl.resolve_target({"prompt": "fix hands"}), "hand")
        self.assertEqual(self.tpl.resolve_target({"prompt": "修一下脸"}), "face")
        self.assertEqual(self.tpl.resolve_target({"prompt": "随便", "mask": "m.png"}),
                         "provided")
        self.assertEqual(self.tpl.resolve_target({"prompt": "随便"}), "hand")

    def test_box_route_uses_real_image_size(self):
        """矩形遮罩必须按真实图像尺寸生成（按 1024 建再缩放会整体偏移，
        实测区域外像素也被改动 4.5%）。"""
        tmp = Path(tempfile.mkdtemp(prefix="boxsize_"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp,
                                                            ignore_errors=True))
        img = tmp / "src.png"
        make_png(img, 920, 1128, 1.0)
        tpl = get_template("local_repair")
        params = tpl.pre_render_fix({"image": str(img), "target": "box",
                                    "box": "0.30,0.45,0.32,0.33", "prompt": "x"})
        self.assertEqual(params.get("_img_w"), 920)
        self.assertEqual(params.get("_img_h"), 1128)
        wf = tpl.render(params)
        m = next(v for v in wf.values()
                 if v["class_type"] == "MaskRectAreaAdvanced")
        self.assertEqual(m["inputs"]["image_width"], 920)
        self.assertEqual(m["inputs"]["image_height"], 1128)
        self.assertAlmostEqual(m["inputs"]["x"], int(0.30 * 920), delta=1)
        self.assertAlmostEqual(m["inputs"]["width"], int(0.32 * 920), delta=1)

    def test_route_nodes_declared_for_all_targets(self):
        for target in ("hand", "face", "box", "provided"):
            self.assertTrue(LocalRepair.ROUTE_NODES.get(target), target)

    def test_route_nodes_exist_on_this_machine(self):
        """四条路线声明的节点必须都在本机快照里（否则自动路由会瞎跑）。"""
        snap_path = config.KNOWLEDGE_DIR / "object_info_snapshot.json"
        if not snap_path.exists():
            self.skipTest("无本机节点快照")
        snap = json.loads(snap_path.read_text(encoding="utf-8"))
        for target, nodes in LocalRepair.ROUTE_NODES.items():
            missing = [n for n in nodes if n not in snap]
            self.assertFalse(missing, f"{target} 路线缺本机节点 {missing}")

    def test_hand_weights_declared(self):
        self.assertIn("hand_yolov8s.pt", self.tpl.models_used)


class TestLocalFixPhrasings(unittest.TestCase):
    """口语说法必须被认成局部修复（实测漏过"修一下手"）。"""

    POSITIVE = ["要修手", "修一下手", "手崩了", "手指畸形", "修脸", "脸崩了",
                "帮我修一下脸", "把那块修一下", "局部有问题", "把袖子去掉",
                "眼睛崩了"]
    NEGATIVE = ["换个风格", "画一张全身照", "重做一张"]

    def test_phrasings(self):
        for t in self.POSITIVE:
            self.assertTrue(TaskContract.parse(t).is_local_fix, t)
        for t in self.NEGATIVE:
            self.assertFalse(TaskContract.parse(t).is_local_fix, t)

    def test_all_positive_phrasings_infer_a_target(self):
        """能被认成局部修复的说法，至少能推出一个可自动定位的目标或明确转问用户。"""
        from brain.agent import Brain
        for t in ("修一下手", "手崩了", "手指畸形"):
            self.assertEqual(Brain._infer_repair_target(t), "hand", t)
        for t in ("修一下脸", "脸崩了"):
            self.assertEqual(Brain._infer_repair_target(t), "face", t)
        # 没有检测目标也没有坐标 → None（转 ask_user 要遮罩）
        self.assertIsNone(Brain._infer_repair_target("把那块修一下"))


class TestMaskGuard(unittest.TestCase):
    """遮罩绝不能被"本轮上传"自动填充：原图当遮罩 = 整图重绘。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="maskguard_"))
        self.upload = self.tmp / "upload.png"
        make_png(self.upload, 32, 32, 0.5)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, params, current_upload):
        from comfy_agent.runner import _ensure_inputs_uploaded
        tpl = get_template("inpaint")            # image + mask 都声明为输入文件
        class Cli:                               # 不连真服务器
            def upload_image(self, p):
                return {"name": Path(p).name}
        return _ensure_inputs_uploaded(
            tpl, dict(params), Cli(), str(self.tmp / "outputs"),
            current_upload=current_upload)

    def test_image_filled_but_mask_refused(self):
        params, notes, err = self._run(
            {}, {"local_path": str(self.upload), "server_name": "upload.png"})
        self.assertIsNotNone(err)
        self.assertIn("遮罩", err)                # 明确要求显式遮罩
        self.assertNotIn("mask", params)          # 没有拿原图当遮罩
        self.assertTrue(any("本轮用户上传" in n for n in notes),
                        "图像参数仍应自动填充")

    def test_explicit_mask_is_kept(self):
        mask = self.tmp / "m.png"
        make_png(mask, 32, 32, 0.2)
        class Cli:
            def upload_image(self, p):
                return {"name": Path(p).name}
        from comfy_agent.runner import _ensure_inputs_uploaded
        tpl = get_template("inpaint")
        params, notes, err = _ensure_inputs_uploaded(
            tpl, {"image": str(self.upload), "mask": str(mask)}, Cli(),
            str(self.tmp / "outputs"))
        self.assertIsNone(err)
        self.assertEqual(params["mask"], "m.png")
        self.assertTrue(params["image"].endswith(".png"))


class TestMaskCoverage(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="maskcov_"))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _png(self, frac, w=200, h=100):
        p = self.tmp / f"m{frac}.png"
        make_png(p, w, h, frac)
        return p

    def test_coverage_and_bbox(self):
        p = self._png(0.25)
        self.assertAlmostEqual(mask_coverage(p), 0.25, places=2)
        x0, y0, x1, y1 = bbox(p)
        self.assertEqual((x0, y0), (0, 0))
        self.assertAlmostEqual(x1, 49, delta=1)
        self.assertEqual(y1, 99)

    def test_empty_mask_is_invalid(self):
        p = self._png(0.0)
        info = check_mask(p)
        self.assertFalse(info["ok"])
        self.assertIn("没有检测到", info["reason"])
        self.assertIn("遮罩", info["hint"])

    def test_full_image_mask_is_rejected(self):
        p = self._png(0.9)
        info = check_mask(p)
        self.assertFalse(info["ok"])
        self.assertIn("局部", info["reason"])

    def test_valid_mask_passes(self):
        info = check_mask(self._png(0.1))
        self.assertTrue(info["ok"])
        self.assertTrue(info["checkable"])

    def test_unreadable_mask_does_not_block(self):
        p = self.tmp / "not.png"
        p.write_bytes(b"not a png")
        info = check_mask(p)
        self.assertTrue(info["ok"])
        self.assertFalse(info["checkable"])


class TestStrategySignatureLocal(unittest.TestCase):
    def test_local_repair_counts_as_inpaint(self):
        sig = strategy_signature("local_repair",
                                 {"image": "a.png", "target": "hand"})
        self.assertEqual(sig[2], "inpaint")

    def test_ledger_sees_local_attempt(self):
        led = AttemptLedger()
        led.record(strategy_signature("i2i", {"image": "a.png"}), {},
                   {"ok": True}, score=6)
        self.assertTrue(led.unrepaired_local_fix())
        led.record(strategy_signature("local_repair", {"image": "a.png"}), {},
                   {"ok": True}, score=8)
        self.assertFalse(led.unrepaired_local_fix())
        self.assertEqual(led.local_repair_targets(), ["local_repair"])


class _FakeWorld:
    def __init__(self, ok=True, why=""):
        self._ok, self._why = ok, why

    def route_available(self, nodes):
        return {"available": self._ok, "present": [] if not self._ok else nodes,
                "missing": [] if self._ok else list(nodes),
                "hints": [self._why] if self._why else []}


class _FakeCtx:
    def __init__(self, world_ok=True, upload=None):
        from comfy_agent.world import WorldModel
        self.world = _FakeWorld(world_ok, "本机没有这个节点" if not world_ok else "")
        self.current_upload = upload
        self.draft_meta = {}
        self.project = None
        self.task = None


class TestAutoRouteConditions(unittest.TestCase):
    """五个条件缺一不可——用真实的 Brain 方法 + 假 ctx 验证。"""

    def setUp(self):
        from brain.agent import Brain
        self.brain = Brain.__new__(Brain)          # 不跑 __init__（不连服务）
        self.brain.ctx = _FakeCtx()
        self.brain.history = []
        self.brain.verbose = False
        self.called = []

    def _task(self, text="要修手", verdict=False, score=6):
        t = TaskState(TaskContract.parse(text))
        t.ledger.record(strategy_signature("i2i", {"image": "a.png"}), {},
                        {"ok": True}, score=score)
        self.brain.ctx.task = t
        self.brain.ctx.draft_meta = {
            "last_outputs": ["C:/out/a.png"],
            "last_eval": {"verdict": verdict, "score": score}}
        return t

    def _spy(self):
        """把 run_template 换成记录调用（execute_tool 走 TOOLS 表）。"""
        from brain import tools as T
        orig = T.TOOLS["run_template"]["fn"]
        def spy(ctx, args):
            self.called.append(args)
            return {"ok": True, "stage": "completed",
                    "evaluation": {"vlm": [{"score": 8}]},
                    "outputs": []}
        T.TOOLS["run_template"] = {**T.TOOLS["run_template"], "fn": spy}
        self.addCleanup(lambda: T.TOOLS.__setitem__(
            "run_template", {**T.TOOLS["run_template"], "fn": orig}))

    def test_all_conditions_met_triggers_hand_repair(self):
        self._task()
        self._spy()
        out = self.brain._auto_local_repair("同手法重掷")
        self.assertIsNotNone(out)
        self.assertEqual(self.called[0]["template_id"], "local_repair")
        self.assertEqual(self.called[0]["params"]["target"], "hand")
        self.assertEqual(self.called[0]["params"]["image"], "C:/out/a.png")
        # 记为 inpaint 手法 → 之后不再判"未做局部"
        self.assertFalse(self.brain.ctx.task.ledger.unrepaired_local_fix())

    def test_not_local_fix_does_not_trigger(self):
        self._task(text="换个风格")
        self._spy()
        self.assertIsNone(self.brain._auto_local_repair("x"))
        self.assertEqual(self.called, [])

    def test_already_tried_local_does_not_retrigger(self):
        t = self._task()
        t.ledger.record(strategy_signature("local_repair", {"image": "a.png"}),
                        {}, {"ok": True}, score=6)
        self._spy()
        self.assertIsNone(self.brain._auto_local_repair("x"))
        self.assertEqual(self.called, [])

    def test_passing_eval_does_not_trigger(self):
        self._task(verdict=True, score=9)
        self._spy()
        self.assertIsNone(self.brain._auto_local_repair("x"))
        self.assertEqual(self.called, [])

    def test_no_base_image_does_not_trigger(self):
        self._task()
        self.brain.ctx.draft_meta["last_outputs"] = []
        self.brain.ctx.current_upload = None
        self._spy()
        self.assertIsNone(self.brain._auto_local_repair("x"))
        self.assertEqual(self.called, [])

    def test_missing_node_does_not_trigger_and_tells_brain(self):
        self._task()
        self.brain.ctx.world = _FakeWorld(False, "本机没有节点 AILab_YoloV8Adv")
        self._spy()
        self.assertIsNone(self.brain._auto_local_repair("x"))
        self.assertEqual(self.called, [])
        self.assertTrue(any("不可用" in str(h.get("content"))
                            for h in self.brain.history))

    def test_undetectable_target_asks_user_for_mask(self):
        self._task(text="把背景那块修一下")     # 不是手也不是脸，且无坐标
        self._spy()
        self.assertIsNone(self.brain._auto_local_repair("x"))
        self.assertEqual(self.called, [])
        self.assertTrue(any("遮罩" in str(h.get("content"))
                            for h in self.brain.history))

    def test_face_target_inferred(self):
        self._task(text="脸崩了，修一下脸")
        self._spy()
        out = self.brain._auto_local_repair("x")
        self.assertIsNotNone(out)
        self.assertEqual(self.called[0]["params"]["target"], "face")

    def test_upload_used_when_no_previous_output(self):
        self._task()
        self.brain.ctx.draft_meta["last_outputs"] = []
        self.brain.ctx.current_upload = {"local_path": "C:/in/up.png"}
        self._spy()
        out = self.brain._auto_local_repair("x")
        self.assertIsNotNone(out)
        self.assertEqual(self.called[0]["params"]["image"], "C:/in/up.png")

    def test_reason_prompt_targets_region_only(self):
        p = self.brain._repair_prompt("hand", "要修手")
        self.assertIn("hands", p)

    def test_dependency_failure_is_reported_as_node_problem(self):
        """节点在但 python 依赖缺失（实测 ultralytics）→ 按节点/依赖问题说清，
        不要让用户去下模型，也不要假装修好。"""
        self._task()
        from brain import tools as T
        orig = T.TOOLS["run_template"]["fn"]
        T.TOOLS["run_template"] = {**T.TOOLS["run_template"], "fn": lambda c, a: {
            "ok": False, "stage": "execution_failed",
            "exec_error": {"class": "AILab_YoloV8Adv",
                           "message": "No module named 'ultralytics'\n"}}}
        self.addCleanup(lambda: T.TOOLS.__setitem__(
            "run_template", {**T.TOOLS["run_template"], "fn": orig}))
        out = self.brain._auto_local_repair("x")
        self.assertIsNotNone(out)
        msg = " ".join(str(h.get("content")) for h in self.brain.history)
        self.assertIn("依赖", msg)
        self.assertIn("遮罩", msg)
        self.assertGreaterEqual(self.brain.ctx.task.wasted, 1)
        self.assertTrue(any("执行失败" in n for n in self.brain.ctx.task.notes))


class TestRepairIsAuxiliaryOutput(unittest.TestCase):
    def test_mask_preview_not_treated_as_product(self):
        from comfy_agent.runner import _is_aux_output
        self.assertTrue(_is_aux_output(
            {"filename": "agent_local_repair_mask_00001_.png"}))
        self.assertFalse(_is_aux_output(
            {"filename": "agent_local_repair_00001_.png"}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
