# -*- coding: utf-8 -*-
"""合成会话（ComfySearch 的 MDP 思路工程化）：

LLM 小步提交编辑 → 原子应用 → 图级校验 → accept（诊断留档）或
reject（整体回滚，原因反馈）。被拒编辑不污染图，诊断进历史。
"""
from __future__ import annotations

import copy

from .graph import Graph
from .validate_graph import validate_graph
from . import ops
from . import patterns as pat


class SynthSession:
    def __init__(self, goal: str, knowledge, base_api: dict = None):
        self.goal = goal
        self.knowledge = knowledge
        self.g = Graph(base_api or {}, knowledge)
        self.history: list[dict] = []      # 每次 propose 的结果
        self._stack: list[dict] = []       # 已接受状态栈（回滚用）

    # ---------- 状态 ----------
    def summary(self) -> str:
        return (f"目标：{self.goal}\n" + (self.g.summary() or "（空图）") +
                f"\n已接受编辑 {len(self._stack)} 次")

    def to_api(self) -> dict:
        return self.g.to_api()

    # ---------- 编辑 ----------
    def propose(self, edits: list[dict]) -> dict:
        """原子应用一批编辑。全部成功才提交；任一失败整体回滚。
        返回 {accepted, diagnostics, history_tail}"""
        snapshot = copy.deepcopy(self.g.api)
        diag = []
        try:
            for edit in edits:
                edit = _normalize_edit(edit)
                op = edit["op"]
                if op == "add_node":
                    # id 缺省自动分配（LLM 不必管理节点号）
                    nid = str(edit.get("id") or self._fresh_id())
                    ops.add_node(self.g, nid, edit["class_type"],
                                 edit.get("inputs"), auto_defaults=True)
                elif op == "set_input":
                    ops.set_input(self.g, str(edit["node"]), edit["input"],
                                  edit["value"])
                elif op == "connect":
                    ops.connect(self.g, str(edit["src"]),
                                int(edit.get("src_slot", 0)),
                                str(edit["dst"]), edit["dst_input"])
                elif op == "disconnect":
                    ops.disconnect(self.g, str(edit["dst"]), edit["input"])
                elif op == "remove_node":
                    ops.remove_node(self.g, str(edit["id"]))
                elif op == "insert_lora":
                    pat.add_lora(self.g, edit["lora_name"],
                                 float(edit.get("strength", 1.0)))
                elif op == "insert_controlnet":
                    pat.add_controlnet(
                        self.g, str(edit["image_node"]),
                        edit.get("controlnet_name"),
                        float(edit.get("strength", 0.8)))
                else:
                    raise ops.EditError(
                        f"未知编辑操作 {op}。合法: {', '.join(_OP_KEYS)}",
                        {"reason": "unknown_op"})
            # 全批应用后图级校验（pending_wiring=分步建图中间态，不算阻断）
            issues = validate_graph(self.g)
            blockers = [i for i in issues
                        if i["reason"] not in ("orphan_output", "pending_wiring")]
            if blockers:
                self.g.api = snapshot     # 回滚
                self.history.append({"edits": edits, "accepted": False,
                                     "diagnostics": blockers})
                return {"accepted": False, "reason": "validation_failed",
                        "diagnostics": blockers,
                        "hint": "按诊断修改后重新 propose（图已回滚）"}
        except ops.EditError as e:
            self.g.api = snapshot
            self.history.append({"edits": edits, "accepted": False,
                                 "diagnostics": [e.diagnostics]})
            return {"accepted": False, "reason": "edit_error",
                    "diagnostics": [e.diagnostics]}

        self._stack.append(snapshot)
        self.history.append({"edits": edits, "accepted": True,
                             "diagnostics": [i for i in diag]})
        return {"accepted": True, "diagnostics": [],
                "nodes": len(self.g.nodes()),
                "hint": f"已接受，当前 {len(self.g.nodes())} 个节点"}

    def undo(self) -> dict:
        if not self._stack:
            return {"ok": False, "error": "没有可回滚的编辑"}
        self.g.api = self._stack.pop()
        return {"ok": True, "note": f"回滚一次，剩余 {len(self._stack)} 层"}

    def recent_history(self, n: int = 3) -> list[dict]:
        return self.history[-n:]

    def _fresh_id(self) -> int:
        used = {int(k) for k in self.g.nodes() if str(k).isdigit()}
        i = max(used) + 1 if used else 100
        while str(i) in self.g.nodes():
            i += 1
        return i


_OP_KEYS = ("add_node", "set_input", "connect", "disconnect",
            "remove_node", "insert_lora", "insert_controlnet")


def _normalize_edit(edit: dict) -> dict:
    """宽容归一化 LLM 提交的编辑格式：
    {"op": "add_node", ...}        标准式
    {"add_node": {...}}            工具名作键
    {"op": "add_node", "node": {...}} 嵌套式
    返回 {op, payload...}。"""
    if not isinstance(edit, dict):
        raise ops.EditError(f"编辑项必须是对象，收到 {type(edit).__name__}",
                            {"reason": "unknown_op"})
    op = edit.get("op")
    if op not in _OP_KEYS:
        for key in _OP_KEYS:
            if key in edit:
                op = key
                inner = edit[key]
                if isinstance(inner, dict):
                    merged = {k: v for k, v in edit.items() if k != key}
                    merged.update(inner)
                    edit = merged
                break
    if op not in _OP_KEYS:
        raise ops.EditError(
            f"未知编辑操作 {op or '(空)'}。合法操作: {', '.join(_OP_KEYS)}。"
            "示例: {\"op\": \"add_node\", \"class_type\": \"KSampler\"}",
            {"reason": "unknown_op"})
    out = dict(edit)
    out["op"] = op
    return out
