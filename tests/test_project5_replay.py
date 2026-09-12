# -*- coding: utf-8 -*-
"""项目 5 回放回归（关键闸门，离线、不烧 GPU）。

素材来自真实日志 `.comfy-agent/projects/proj_ef2d127d/history.jsonl`
（2026-09-12 20:13→23:55，9033 行）。这里不回放 LLM，而是把日志里**真实发生过
的决策输入**喂给新的决策层，断言新系统会拦住当时造成浪费的行为：

  ① 同一基底图连着重绘三次（只改 denoise）→ 第 3 次被拒
  ② "全身照"这个要求进评估判据 → 特写不能算达标
  ③ 下 hand_yolov8s.pt 前就查出本机没有能走"检测器→遮罩"链路的节点
  ④ 不再编造"刚才因缺模型失败的任务"
  ⑤ 有待确认弹窗的那一轮仍然交付文本
  ⑥ 空参数工具调用给可读错误

日志不在时跳过（CI/换机），但本地必须跑。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PROJECT5 = ROOT / ".comfy-agent" / "projects" / "proj_ef2d127d"
LOG = PROJECT5 / "history.jsonl"

# 日志里真实出现的三次重绘（同一基底 agent_i2i_00017_.png，只改 denoise/seed）
REAL_REROLLS = [
    {"image": "agent_i2i_00017_.png", "denoise": 0.35, "seed": 778899},
    {"image": "agent_i2i_00017_.png", "denoise": 0.45, "seed": 445566},
    {"image": "agent_i2i_00017_.png", "denoise": 0.50, "seed": 112233},
]
# 日志里真实拿到的分数（6 → 4 → 6）
REAL_SCORES = [6, 4, 6]
REAL_USER_TURN = "生成一个差不多风格的动漫少女全身照"
REAL_DOWNLOAD = {"filename": "hand_yolov8s.pt", "folder": "ultralytics",
                 "route": ["UltralyticsDetectorProvider", "BboxDetectorSEGS"]}


def _read_log():
    if not LOG.exists():
        return None
    return [json.loads(l) for l in LOG.read_text(encoding="utf-8").splitlines()
            if l.strip()]


class TestProject5Replay(unittest.TestCase):
    """断言的是机制，不是提示词；全部离线。"""

    def setUp(self):
        rows = _read_log()
        if rows is None:
            self.skipTest(f"项目 5 日志不在本机：{LOG}")
        self.rows = rows
        self.tmp = Path(tempfile.mkdtemp(prefix="p5_"))
        from comfy_agent import config
        self._old_models = config.MODELS_DIR
        config.MODELS_DIR = self.tmp          # 隔离：不读真机模型
        # 世界模型测试要隔离快照路径
        from comfy_agent import knowledge as kmod
        self._km = kmod
        self._old_snap = kmod.SNAPSHOT_PATH
        self._old_meta = kmod.SNAPSHOT_META_PATH
        kmod.SNAPSHOT_PATH = self.tmp / "snap.json"
        kmod.SNAPSHOT_META_PATH = self.tmp / "snap.meta.json"

    def tearDown(self):
        import shutil
        from comfy_agent import config
        config.MODELS_DIR = self._old_models
        self._km.SNAPSHOT_PATH = self._old_snap
        self._km.SNAPSHOT_META_PATH = self._old_meta
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---------- ① 连着重绘 ----------
    def test_three_real_rerolls_would_be_blocked(self):
        from brain.task import AttemptLedger, strategy_signature
        led = AttemptLedger()
        actions = []
        for prm, score in zip(REAL_REROLLS, REAL_SCORES):
            sig = strategy_signature("i2i", prm)
            v = led.check(sig)
            actions.append(v["action"])
            if v["allow"]:
                led.record(sig, prm, {"ok": True}, score=score)
        self.assertEqual(actions[0], "run")
        self.assertEqual(actions[1], "must_change")   # 第 2 次要求换手法
        self.assertEqual(actions[2], "blocked")       # 再坚持同一手法就硬拦
        # 只渲染了 1 次（当时渲染了 3 次）→ 省下 2 次整图重绘
        self.assertEqual(len(led.attempts), 1)
        # 分数轨迹 6→4（当时的真实结果）必须被识别为"没有提升"
        led2 = AttemptLedger()
        from brain.task import strategy_signature as sig_of
        s = sig_of("i2i", REAL_REROLLS[0])
        led2.record(s, REAL_REROLLS[0], {"ok": True}, score=REAL_SCORES[0])
        led2.record(s, REAL_REROLLS[1], {"ok": True}, score=REAL_SCORES[1])
        self.assertTrue(led2.regressed())

    def test_real_rerolls_share_one_signature(self):
        """三次的签名必须相同，否则护栏形同虚设（这是当时漏掉的根因）。"""
        from brain.task import strategy_signature
        sigs = {strategy_signature("i2i", p) for p in REAL_REROLLS}
        self.assertEqual(len(sigs), 1)

    # ---------- ② 判据按用户要求 ----------
    def test_full_body_requirement_enters_criteria(self):
        from brain.task import TaskContract
        c = TaskContract.parse(REAL_USER_TURN, uses_upload=True)
        self.assertIn("必须全身：从头到脚完整可见（含脚/鞋）", c.checks)
        self.assertIn("全身", c.criteria())
        # 当时用的是通用套话，所以特写拿 9/10；新判据必须点出"全身"
        self.assertNotEqual(c.criteria(), "画面清晰完整、主体明确、无畸形")

    def test_forced_eval_uses_contract_and_reports_criteria(self):
        """（离线）强制评估必须带上契约判据，且 VLM 不可用时不得报通过。"""
        import comfy_agent.runner as R
        from comfy_agent import config
        tmp = self.tmp / "img.png"
        tmp.write_bytes(bytes.fromhex(
            "89504e470d0a1a0a0000000d4948445200000008000000080806000000"
            "1f15c4890000000a49444154789c6360000002000100ffff03000006"
            "0005574bd3a20000000049454e44ae426082"))
        seen = {}
        import brain.eval as E

        class _Res:
            def to_dict(self):
                return {"tier0": [{"file": str(tmp), "pass": True}], "vlm": [],
                        "ok": True, "advice": "", "vlm_error": "LLM 连接中断"}
        orig = E.evaluate
        E.evaluate = lambda paths, crit, sample=None: (seen.setdefault("crit", crit),
                                                       _Res())[1]
        events = []
        import brain.events as bev
        orig_emit = bev.emit
        bev.emit = lambda name, data=None: events.append((name, data))
        try:
            result = {"prompt_id": "p1"}
            R._force_image_evaluation(result, [{"local_path": str(tmp)}],
                                      "必须全身：从头到脚完整可见（含脚/鞋）")
        finally:
            E.evaluate = orig
            bev.emit = orig_emit
        self.assertIn("全身", seen["crit"])
        ev = result["evaluation"]
        self.assertIsNone(ev["verdict"])            # 语义没查 → 不报通过
        self.assertFalse(ev["semantic_checked"])
        self.assertIn("全身", ev["criteria"])
        self.assertEqual(events[0][1]["criteria"], ev["criteria"])

    # ---------- ③ 下载前查清链路 ----------
    def test_download_precheck_reports_missing_loader_node(self):
        from comfy_agent.world import WorldModel
        from comfy_agent.knowledge import Knowledge
        from tests.test_synth import SNAPSHOT
        # 本机有 AILab_YoloV8Adv（能读 ultralytics 模型）但没有 Impact 的
        # UltralyticsDetectorProvider —— 正是项目 5 的真实情况
        snap = dict(SNAPSHOT)
        snap["AILab_YoloV8Adv"] = {"input": {"required": {}}}
        k = Knowledge(snap, {}, {"ultralytics": []})
        w = WorldModel(knowledge=k)

        coarse = w.model_usability(REAL_DOWNLOAD["filename"],
                                  REAL_DOWNLOAD["folder"])
        self.assertTrue(coarse["usable"])            # 模型本身有人能加载
        route = w.route_available(REAL_DOWNLOAD["route"])
        self.assertFalse(route["available"])         # 但想要的链路不可用
        self.assertIn("UltralyticsDetectorProvider", route["missing"])
        self.assertTrue(any("未注册" in h or "没有这个节点" in h
                            for h in route["hints"]))

    def test_download_finished_does_not_invent_premise(self):
        """日志里那次下载与"因缺模型失败"无关：系统消息不得这么说。"""
        from brain.web import server

        class _BS:
            def __init__(self):
                import queue
                self.inbox = queue.Queue()

        class _Sess:
            default_id = "p"

            def __init__(self):
                self.bs = _BS()
                self.brain = type("B", (), {"ctx": type(
                    "C", (), {"refresh_world": staticmethod(lambda *a: None)})()})()

            def session_for(self, pid=None):
                return self.bs

        old = server.SESSION
        server.SESSION = _Sess()
        try:
            server._download_finished({
                "state": "done", "filename": REAL_DOWNLOAD["filename"],
                "size_text": "21.46 MB", "dest": "D:/models/ultralytics/x.pt",
                "project": "p", "retry": {}})         # 没有真实缺模型记录
            text = server.SESSION.bs.inbox.get_nowait()["text"]
        finally:
            server.SESSION = old
        self.assertNotIn("请立即重新执行", text)
        self.assertIn("已就绪", text)

    def test_real_missing_model_still_reruns(self):
        """真发生过缺模型失败时，仍要按记录重跑（别修成永不重跑）。"""
        from brain.web import server
        import queue

        class _BS:
            def __init__(self):
                self.inbox = queue.Queue()

        class _Sess:
            default_id = "p"

            def __init__(self):
                self.bs = _BS()
                self.brain = type("B", (), {"ctx": type(
                    "C", (), {"refresh_world": staticmethod(lambda *a: None)})()})()

            def session_for(self, pid=None):
                return self.bs

        old = server.SESSION
        server.SESSION = _Sess()
        server._AUTO_RETRY.clear()
        try:
            server._download_finished({
                "state": "done", "filename": "ltx.safetensors",
                "size_text": "9 GB", "dest": "D:/models/checkpoints/ltx.safetensors",
                "project": "p",
                "retry": {"template": "ltx_i2v", "params": {"image": "a.png"},
                          "from_missing_model": True}})
            text = server.SESSION.bs.inbox.get_nowait()["text"]
        finally:
            server.SESSION = old
            server._AUTO_RETRY.clear()
        self.assertIn("因缺模型失败", text)
        self.assertIn("ltx_i2v", text)

    # ---------- ④ 空参数友好报错 ----------
    def test_empty_args_give_readable_errors(self):
        from comfy_agent.runner import run_template
        from comfy_agent.knowledge import Knowledge
        from tests.test_synth import SNAPSHOT
        r = run_template("", {}, knowledge=Knowledge(dict(SNAPSHOT), {}, {}),
                         client=object(), wait=False)
        self.assertFalse(r["ok"])
        self.assertIn("template_id", r["error"])       # 不是"未知模板: "

    # ---------- ⑤ 待确认弹窗那一轮必须交付 ----------
    def test_turn_with_pending_confirmation_still_delivers(self):
        """用脚本化 LLM 跑一轮：工具返回 awaiting_confirm，仍必须有 delivery。"""
        from brain.agent import Brain
        from brain.events import EventBus
        import brain.events as bev

        events = []
        bus = EventBus()
        bus.subscribe(lambda m: events.append(m))
        orig_bus = bev.bus
        bev.bus = bus
        try:
            b = Brain(verbose=False)
            b.llm = _ScriptedLLM([
                # 第 1 轮：请求下载（工具会返回 awaiting_confirm）
                '好的，我来查。\n```json\n{"tool": "search_models", '
                '"args": {"filename": "hand_yolov8s.pt", "offline": true}}\n```',
                # 第 2 轮：给出结论（没有工具调用 → 交付）
                '模型来源已找到，等你在弹窗里确认是否下载。',
            ])
            # 让 search_models 变成"待确认"工具
            b.ctx.task = None
            from brain import tools as T
            orig = T.TOOLS["search_models"]
            T.TOOLS["search_models"] = {**orig, "fn": lambda ctx, args: {
                "ok": True, "awaiting_confirm": True, "download_id": "d1",
                "note": "等用户确认"}}
            try:
                reply = b.handle("下载手部检测模型")
            finally:
                T.TOOLS["search_models"] = orig
            deliveries = [m for m in events if m["event"] == "delivery"]
            self.assertTrue(deliveries, "有待确认弹窗时也必须交付")
            self.assertIn("等你在弹窗里决定", deliveries[-1]["data"]["text"])
        finally:
            bev.bus = orig_bus


class _ScriptedLLM:
    """按脚本逐轮返回文本的假 LLM（不联网）。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.base = "https://fake"
        self.model = "fake"
        self.key = "k"

    @property
    def ready(self):
        return True

    def chat_stream(self, messages, temperature=0.6, max_tokens=4096,
                    thinking=None):
        text = self.replies.pop(0) if self.replies else "（结束）"
        yield ("content", text)

    def chat(self, messages, temperature=0.6, max_tokens=4096, thinking=None):
        return self.replies.pop(0) if self.replies else "（结束）"

    def reload_settings(self):
        return {}


if __name__ == "__main__":
    unittest.main(verbosity=2)
