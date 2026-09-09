# -*- coding: utf-8 -*-
"""图级校验（合成期专用，比 validate_workflow 更细的连线/类型/环检查）。

返回结构化诊断列表 [{node, input, reason, hint}]——ComfySearch 风格：
被拒编辑不改图，诊断进历史供下一轮修复。
"""
from __future__ import annotations

from .graph import Graph, parse_link
from ..validate import ValidationIssue


def validate_graph(g: Graph) -> list[dict]:
    issues: list[dict] = []

    if g.has_cycle():
        issues.append({"node": None, "reason": "cycle",
                       "hint": "图中存在环，ComfyUI 工作流必须是 DAG"})

    for nid, node in g.api.items():
        cls = node.get("class_type", "")
        info = g.info_of(nid)
        if info is None:
            issues.append({"node": nid, "class": cls, "reason": "unknown_node",
                           "hint": f"节点 {cls} 本机不存在"})
            continue
        inputs = node.get("inputs", {})
        required = list(info.get("input", {}).get("required", {}).keys())
        # 必填缺失：标量缺失=真阻断；连线型输入未接=中间态（pending_wiring，
        # 分步建图允许，最终 submit 前必须接齐）
        for name in required:
            if name not in inputs:
                spec = info.get("input", {}).get("required", {}).get(name)
                t = spec[0] if isinstance(spec, list) and spec else None
                is_scalar = isinstance(t, list) or \
                    (isinstance(t, str) and t in
                     ("INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"))
                issues.append({
                    "node": nid, "class": cls, "input": name,
                    "reason": "missing_required" if is_scalar else "pending_wiring",
                    "hint": f"必填输入 {name} 缺失"
                    + ("（连线或赋值）" if is_scalar else "（待接线）")})
        # 连线校验
        for name, val in inputs.items():
            spec = g.input_spec(nid, name)
            if spec is None:
                issues.append({"node": nid, "class": cls, "input": name,
                               "reason": "unknown_input",
                               "hint": f"输入 {name} 不在节点定义中"})
                continue
            link = parse_link(val)
            if link is None:
                continue
            src_node = g.api.get(link.src)
            if src_node is None:
                issues.append({"node": nid, "class": cls, "input": name,
                               "reason": "dangling_link",
                               "hint": f"连线源 {link.src} 不存在"})
                continue
            got = g.output_type(link.src, link.slot)
            if got is None:
                issues.append({"node": nid, "class": cls, "input": name,
                               "reason": "bad_slot",
                               "hint": f"源 {link.src} 没有输出槽 {link.slot}"})
                continue
            want = g.input_type(nid, name)
            if want and got and want != got:
                issues.append({"node": nid, "class": cls, "input": name,
                               "reason": "type_mismatch",
                               "hint": f"{name} 需要 {want} 但 {link.src}"
                                       f"[{link.slot}] 输出 {got}"})
        # 断链残留：值为 None 的已连线输入（connect 失败回滚产物）
        for name, val in list(inputs.items()):
            if val is None:
                if name in required:
                    spec = info.get("input", {}).get("required", {}).get(name)
                    t = spec[0] if isinstance(spec, list) and spec else None
                    is_scalar = isinstance(t, list) or \
                        (isinstance(t, str) and t in
                         ("INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"))
                    issues.append({"node": nid, "class": cls, "input": name,
                                   "reason": "missing_required"
                                   if is_scalar else "pending_wiring",
                                   "hint": f"必填输入 {name} 断链（值为空）"})
                del inputs[name]

    # 无下游的死产出节点提示（不算错误，作为提示）
    for nid in g.nodes():
        if g.class_of(nid) in ("SaveImage", "PreviewImage", "SaveVideo"):
            continue
        if not g.sinks_of(nid) and not g.links_from(nid) and \
                g.outputs(nid) and len(g.nodes()) > 1:
            issues.append({"node": nid, "class": g.class_of(nid),
                           "reason": "orphan_output",
                           "hint": "产出节点无下游（可能是中间节点或漏连线）"})

    return issues


def is_valid(g: Graph) -> bool:
    return not any(i["reason"] not in ("orphan_output",)
                   for i in validate_graph(g))
