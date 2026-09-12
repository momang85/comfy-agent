# -*- coding: utf-8 -*-
"""任务契约与尝试台账：把"用户要什么"和"已经试过什么"变成机器可检查的状态。

为什么需要（项目 5 实证）：
- 需求只以散文存在于对话里，摘要评估用通用套话 → "全身照"从未进入判据，
  特写拿了 9/10，用户 20:15 直接反驳"你这并不是全身照"。
- 护栏只看"完全同参重复"，改个 denoise 就是新签名 → 同一张图连着重绘 3 次
  （6→4→6→6），`repairs:[]` 说明没有任何机制管"语义不收敛"。
- 没有任何预算概念，只能靠提示词"别烧 GPU"。

设计：回合开始建 TaskState（契约 + 台账 + 预算），引擎在**执行前**用
`AttemptLedger.check()` 拦截无效重绘；评估结果回写台账，用于"分数不提升就停"。
提示词只负责不可机检的判断，机检部分全部下沉到这里。
"""
from __future__ import annotations

import re
import time

# 需求里可机检的约束词 → 判据（进评估 criteria，也进交付对照）
CONSTRAINT_PATTERNS: list[tuple[str, str]] = [
    (r"全身|full[- ]?body", "必须全身：从头到脚完整可见（含脚/鞋）"),
    (r"半身|上半身", "半身构图：腰部以上"),
    (r"特写|大特写|close[- ]?up", "特写构图：脸部/局部为主"),
    (r"头像|1:1|方形", "头像/方形构图"),
    (r"竖版|竖构图|portrait", "竖版构图"),
    (r"横版|横构图|landscape|16:9", "横版/宽幅构图"),
    (r"保持.{0,4}(姿势|pose)|锁.{0,2}姿势", "保持原姿势不变"),
    (r"保持.{0,4}(构图|composition)|锁.{0,2}构图", "保持原构图不变"),
    (r"相同?画风|同风格|一样.{0,2}风格|差不多风格", "画风与参考图一致"),
    (r"修手|手部|手指", "手部结构正常（无明显畸形/融合）"),
    (r"修脸|面部|脸崩", "面部正常（五官无畸变）"),
    (r"放大|超分|两倍|2x", "分辨率放大且细节不糊"),
    (r"抠图|去背景|透明背景", "主体抠出、背景干净"),
    (r"无水印|不要水印", "无水印/无多余文字"),
    (r"景深|虚化", "景深/背景虚化符合要求"),
    (r"(\d+)\s*秒", "时长满足要求"),
    (r"白色背景|纯色背景", "背景为纯色"),
    (r"同一个?角色|同一个人", "角色外观一致（发色/瞳色/服饰）"),
]

# 约束词 → 应当走的链路（用于"局部问题别整图重绘"这类判断）
LOCAL_FIX_PATTERN = re.compile(
    r"修手|手部|手指|修脸|脸崩|面部|局部|扣掉|去掉|抹掉|把脸|脸.*修|修.*脸")


class TaskContract:
    """一条需求的可检查形态。"""

    def __init__(self, source_text: str = "", deliverable: str = "",
                 constraints: list[str] = None, checks: list[str] = None,
                 uses_upload: bool = False):
        self.source_text = source_text
        self.deliverable = deliverable
        self.constraints = constraints or []
        self.checks = checks or []
        self.uses_upload = uses_upload
        self.created = time.time()

    @classmethod
    def parse(cls, text: str, uses_upload: bool = False) -> "TaskContract":
        """从用户话里抽出可机检的约束（不依赖 LLM：正则足够稳定且可测试）。"""
        t = str(text or "")
        constraints, checks = [], []
        for pattern, rule in CONSTRAINT_PATTERNS:
            if re.search(pattern, t, re.IGNORECASE):
                if rule not in constraints:
                    constraints.append(rule)
                    checks.append(rule)
        deliverable = ""
        if re.search(r"视频|动画|动起来|短片", t):
            deliverable = "video"
        elif re.search(r"图|画|照片|海报|壁纸|头像|全身|特写", t):
            deliverable = "image"
        return cls(source_text=t, deliverable=deliverable,
                   constraints=constraints, checks=checks,
                   uses_upload=uses_upload)

    @property
    def is_local_fix(self) -> bool:
        return bool(LOCAL_FIX_PATTERN.search(self.source_text))

    def criteria(self, fallback: str = "") -> str:
        """给评估用的判据文本（用户要求优先，没有才用兜底通用标准）。"""
        if self.checks:
            return "；".join(self.checks)
        return fallback or "画面清晰完整、主体明确、无畸形"

    def brief(self) -> str:
        return (f"交付物={self.deliverable or '未定'}"
                f"；约束={self.constraints or '无'}")

    def to_dict(self) -> dict:
        return {"source_text": self.source_text[:200],
                "deliverable": self.deliverable,
                "constraints": self.constraints, "checks": self.checks,
                "uses_upload": self.uses_upload}


def strategy_signature(template_id: str, params: dict) -> tuple:
    """策略签名：同模板 + 同基底图 + 同手段。

    刻意**不含** denoise/steps/cfg/seed —— 只改这些等于同一手法的重掷，
    正是项目 5 里连着重绘三次却拿不到提升的情形。
    """
    params = params or {}
    base = ""
    for key in ("image", "video", "video_path", "source", "init_image"):
        if params.get(key):
            base = str(params[key])
            break
    if not base:                       # 没有基底图时用提示词首段当"题材"
        base = str(params.get("prompt") or "")[:80]
    # 局部修复与整图重绘是两种手法，签名要能区分
    mode = "inpaint" if (params.get("mask") or params.get("grow_mask_by")
                         is not None) else "global"
    return (str(template_id), base, mode)


class AttemptLedger:
    """按策略签名记账，并给出"下一步允许做什么"。"""

    #: 同一策略允许的执行次数（第 2 次要求换手法，第 3 次拒绝）
    MAX_SAME_STRATEGY = 2

    def __init__(self):
        self.attempts: list[dict] = []
        self.signature_counts: dict[tuple, int] = {}
        self.refusals: dict[tuple, int] = {}
        self.used_modes: set[str] = set()

    def record(self, signature: tuple, params: dict, result: dict,
               score: float | None = None, verdict: bool | None = None,
               reason: str = "") -> None:
        self.signature_counts[signature] = \
            self.signature_counts.get(signature, 0) + 1
        self.used_modes.add(signature[2] if len(signature) > 2 else "global")
        self.attempts.append({
            "ts": time.time(), "signature": signature,
            "count": self.signature_counts[signature],
            "score": score, "verdict": verdict, "reason": reason,
            "prompt_head": str((params or {}).get("prompt") or "")[:60],
            "denoise": (params or {}).get("denoise"),
            "ok": bool((result or {}).get("ok")),
            "stage": (result or {}).get("stage"),
        })

    def check(self, signature: tuple) -> dict:
        """执行前的闸门：允许 / 要求换手法 / 拒绝。

        计数包含**被拒的次数**：只用"已执行次数"永远升级不到 blocked
        （模型无视一次警告后还是能一直重掷），所以第二次仍坚持同一手法就硬拦。
        """
        n = self.signature_counts.get(signature, 0)
        if n == 0:
            return {"allow": True, "action": "run", "reason": ""}
        mode = signature[2] if len(signature) > 2 else "global"
        refused = self.refusals.get(signature, 0)
        self.refusals[signature] = refused + 1
        alternatives = (["inpaint（只重绘问题区域，加 mask）"]
                        if mode != "inpaint" else
                        ["换模板/换链路", "ask_user 让用户提供遮罩或换图"])
        if refused >= 1 or n >= self.MAX_SAME_STRATEGY:
            return {"allow": False, "action": "blocked",
                    "reason": (f"同一手法（{signature[0]} + 同一基底）已执行 {n} 次"
                               f"、已提醒 {refused} 次仍未换手法：拒绝继续重掷"),
                    "alternatives": alternatives}
        return {"allow": False, "action": "must_change",
                "reason": (f"该手法已执行 {n} 次：只改 denoise/seed 的重掷无效，"
                           "必须换手法（局部修复用 inpaint+mask、换模板、"
                           "或 ask_user）"),
                "alternatives": alternatives}

    def regressed(self, min_delta: float = 0.5) -> bool:
        """连续两次拿到分数且没有提升 → 该停了。"""
        scored = [a["score"] for a in self.attempts if a.get("score") is not None]
        if len(scored) < 2:
            return False
        return scored[-1] - scored[-2] < min_delta

    def unrepaired_local_fix(self) -> bool:
        """已试过整图重绘但没试过局部修复（局部问题应当走局部）。"""
        return "global" in self.used_modes and "inpaint" not in self.used_modes

    def brief(self) -> str:
        if not self.attempts:
            return "尚无尝试"
        lines = []
        for a in self.attempts[-5:]:
            s = f"{a['score']}" if a.get("score") is not None else "无分"
            lines.append(f"- {a['signature'][0]}({a['signature'][2]}) 第{a['count']}次 "
                         f"denoise={a.get('denoise')} → {'ok' if a['ok'] else a.get('stage')} 分数{s}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {"attempts": [{k: (list(v) if isinstance(v, tuple) else v)
                              for k, v in a.items()} for a in self.attempts],
                "modes": sorted(self.used_modes)}


class TaskState:
    """一轮任务的完整状态：契约 + 台账 + 阶段。"""

    PHASES = ("planning", "awaiting_user", "executing", "evaluating",
              "delivered")

    def __init__(self, contract: TaskContract):
        self.contract = contract
        self.ledger = AttemptLedger()
        self.phase = "planning"
        self.plan: dict | None = None
        self.renders = 0
        self.wasted = 0
        self.ignored_params: list[str] = []
        self.notes: list[str] = []

    def set_phase(self, phase: str) -> None:
        if phase in self.PHASES:
            self.phase = phase

    def to_dict(self) -> dict:
        return {"phase": self.phase,
                "contract": self.contract.to_dict(),
                "plan": self.plan,
                "renders": self.renders, "wasted": self.wasted,
                "ignored_params": self.ignored_params,
                "notes": self.notes[-10:],
                "ledger": self.ledger.to_dict()}
