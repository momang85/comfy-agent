# -*- coding: utf-8 -*-
"""图编辑原语：每步先校验后落盘，失败抛 EditError（含诊断），图不被污染。"""
from __future__ import annotations

from typing import Any, Optional

from .graph import Graph, Link, parse_link


class EditError(Exception):
    def __init__(self, message: str, diagnostics: dict = None):
        super().__init__(message)
        self.diagnostics = diagnostics or {"reason": message}


def add_node(g: Graph, nid: str, class_type: str,
             inputs: dict = None, auto_defaults: bool = True) -> None:
    """加节点。auto_defaults=True 时按 object_info 自动填必填标量的默认值
    （枚举取第一项，INT/FLOAT 取 default 或 0/1.0）。"""
    info = g.knowledge.node_info(class_type)
    if info is None:
        raise EditError(f"节点 {class_type} 在本机不存在",
                        {"reason": "unknown_node", "class_type": class_type,
                         "hint": "用 inspect_node 查可用节点"})
    if str(nid) in g.api:
        raise EditError(f"节点号 {nid} 已被占用")
    node = {"class_type": class_type, "inputs": dict(inputs or {})}
    if auto_defaults:
        for section in ("required", "optional"):
            for name, spec in info.get("input", {}).get(section, {}).items():
                if name in node["inputs"]:
                    continue
                node["inputs"][name] = _default_for(spec)
    g.api[str(nid)] = node


def _default_for(spec) -> Any:
    """按 spec 推断标量默认值；连线型输入返回 None 占位。"""
    if not (isinstance(spec, list) and spec):
        return None
    t = spec[0]
    opts = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
    if isinstance(t, list):
        return t[0] if t else None          # 枚举
    if "default" in opts:
        return opts["default"]
    if t == "INT":
        return 0
    if t == "FLOAT":
        return 1.0
    if t == "STRING":
        return ""
    if t == "BOOLEAN":
        return False
    return None                             # 连线型（MODEL/IMAGE...）留空待接


def set_input(g: Graph, nid: str, name: str, value) -> None:
    node = g.api.get(str(nid))
    if node is None:
        raise EditError(f"节点 {nid} 不存在",
                        {"reason": "unknown_node", "node": nid})
    spec = g.input_spec(nid, name)
    if spec is None:
        raise EditError(
            f"节点 {g.class_of(nid)}(#{nid}) 没有输入 {name}",
            {"reason": "unknown_input", "node": nid, "input": name,
             "hint": f"可用输入: {list(node.get('inputs', {}).keys())}"})
    node.setdefault("inputs", {})[name] = value


def connect(g: Graph, src: str, src_slot: int, dst: str,
            dst_input: str) -> None:
    """类型校验连线：源存在、槽位合法、输出类型 == 输入类型。"""
    if str(src) not in g.api:
        raise EditError(f"源节点 {src} 不存在",
                        {"reason": "unknown_node", "node": src})
    node = g.api.get(str(dst))
    if node is None:
        raise EditError(f"目标节点 {dst} 不存在",
                        {"reason": "unknown_node", "node": dst})
    spec = g.input_spec(dst, dst_input)
    if spec is None:
        raise EditError(f"节点 {g.class_of(dst)}(#{dst}) 没有输入 {dst_input}",
                        {"reason": "unknown_input", "node": dst,
                         "input": dst_input})
    want = g.input_type(dst, dst_input)
    got = g.output_type(src, src_slot)
    if got is None:
        raise EditError(
            f"源 {g.class_of(src)}(#{src}) 没有输出槽 {src_slot}",
            {"reason": "bad_slot", "node": src, "slot": src_slot})
    if want and got and want != got:
        raise EditError(
            f"类型不匹配：{g.class_of(dst)}(#{dst}).{dst_input} 需要 {want}，"
            f"而 {g.class_of(src)}(#{src})[{src_slot}] 输出 {got}",
            {"reason": "type_mismatch", "src": src, "dst": dst,
             "input": dst_input, "expected": want, "got": got})
    node.setdefault("inputs", {})[dst_input] = [str(src), src_slot]
    # 连线后立即环检测（DAG 保证）
    if g.has_cycle():
        node["inputs"][dst_input] = None  # 回滚这条线
        raise EditError(f"连线 {src}→{dst} 会形成环，已拒绝",
                        {"reason": "cycle"})


def disconnect(g: Graph, dst: str, input_name: str) -> None:
    node = g.api.get(str(dst))
    if node is None:
        raise EditError(f"节点 {dst} 不存在", {"reason": "unknown_node"})
    if input_name in node.get("inputs", {}):
        del node["inputs"][input_name]


def remove_node(g: Graph, nid: str) -> None:
    if str(nid) not in g.api:
        raise EditError(f"节点 {nid} 不存在", {"reason": "unknown_node"})
    # 断开下游引用（下游该输入会缺失——由校验层报告）
    for dst, name, _ in g.links_from(nid):
        del g.api[dst]["inputs"][name]
    del g.api[str(nid)]


def insert_between(g: Graph, src: str, src_slot: int, dst: str,
                   dst_input: str, new_id: str, class_type: str,
                   wire_out: str, wire_in: str, extra: dict = None) -> None:
    """A→B 改为 A→X→B：断开原线，X 的 wire_in 接 A，B 的 dst_input 接 X 的
    wire_out（槽 0）。extra 是 X 的其他输入值。"""
    link = g.link_at(dst, dst_input)
    if link is None or link.src != str(src) or link.slot != src_slot:
        raise EditError(f"{(src, src_slot)}→{dst}.{dst_input} 不是现有连线",
                        {"reason": "no_such_link"})
    inputs = dict(extra or {})
    inputs[wire_in] = [str(src), src_slot]
    add_node(g, new_id, class_type, inputs=inputs, auto_defaults=True)
    disconnect(g, dst, dst_input)
    connect(g, new_id, 0, dst, dst_input)   # X 的槽0输出接 B
