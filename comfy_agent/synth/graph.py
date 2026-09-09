# -*- coding: utf-8 -*-
"""工作流图模型：节点/连线/类型系统。

图存 API 格式（{node_id: {class_type, inputs}}，连线为 [src_id, slot]），
类型信息来自 object_info（input 的 spec[0] 与 output 数组）。
"""
from __future__ import annotations

from typing import Any, Optional


class Link:
    __slots__ = ("src", "slot")

    def __init__(self, src: str, slot: int):
        self.src = src
        self.slot = slot

    def as_list(self) -> list:
        return [self.src, self.slot]

    def __eq__(self, other):
        return isinstance(other, Link) and self.src == other.src and \
            self.slot == other.slot

    def __repr__(self):
        return f"Link({self.src}[{self.slot}])"


def parse_link(val) -> Optional[Link]:
    if isinstance(val, list) and len(val) == 2 and isinstance(val[0], str) \
            and isinstance(val[1], int):
        return Link(val[0], val[1])
    return None


class Graph:
    """API 格式工作流的图视图。"""

    def __init__(self, api: dict, knowledge):
        self.api = {str(k): dict(v) for k, v in api.items()}
        self.knowledge = knowledge

    # ---------- 结构 ----------
    def nodes(self) -> list[str]:
        return list(self.api.keys())

    def class_of(self, nid: str) -> Optional[str]:
        node = self.api.get(str(nid))
        return node.get("class_type") if node else None

    def info_of(self, nid: str):
        cls = self.class_of(nid)
        return self.knowledge.node_info(cls) if cls else None

    # ---------- 类型系统 ----------
    def input_spec(self, nid: str, name: str):
        info = self.info_of(nid)
        if not info:
            return None
        for section in ("required", "optional"):
            spec = info.get("input", {}).get(section, {}).get(name)
            if spec is not None:
                return spec
        return None

    def input_type(self, nid: str, name: str) -> Optional[str]:
        spec = self.input_spec(nid, name)
        if spec and isinstance(spec, list) and spec:
            t = spec[0]
            return None if isinstance(t, list) else t
        return None

    def outputs(self, nid: str) -> list[str]:
        """输出类型列表（按槽序）。"""
        info = self.info_of(nid)
        if not info:
            return []
        out = info.get("output") or []
        return [str(t) for t in out]

    def output_type(self, nid: str, slot: int) -> Optional[str]:
        outs = self.outputs(nid)
        return outs[slot] if 0 <= slot < len(outs) else None

    # ---------- 连线 ----------
    def links_from(self, src: str) -> list[tuple[str, str, Link]]:
        """从 src 出发的所有连线 [(dst, dst_input, Link)]。"""
        out = []
        for nid, node in self.api.items():
            for name, val in node.get("inputs", {}).items():
                link = parse_link(val)
                if link and link.src == str(src):
                    out.append((str(nid), name, link))
        return out

    def link_at(self, dst: str, input_name: str) -> Optional[Link]:
        node = self.api.get(str(dst), {})
        return parse_link(node.get("inputs", {}).get(input_name))

    def is_linked(self, dst: str, input_name: str) -> bool:
        return self.link_at(dst, input_name) is not None

    def free_inputs(self, nid: str) -> list[str]:
        """未连线、可赋值/可接线标量的输入名（含必填未填项）。"""
        info = self.info_of(nid) or {}
        node = self.api.get(str(nid), {})
        out = []
        for section in ("required", "optional"):
            for name in info.get("input", {}).get(section, {}):
                if not self.is_linked(nid, name):
                    out.append(name)
        return out

    def sources_of(self, nid: str) -> list[str]:
        node = self.api.get(str(nid), {})
        seen = []
        for val in node.get("inputs", {}).values():
            link = parse_link(val)
            if link and link.src not in seen:
                seen.append(link.src)
        return seen

    def sinks_of(self, nid: str) -> list[str]:
        return [d for d, _, _ in self.links_from(nid)]

    # ---------- 环检测 ----------
    def has_cycle(self) -> bool:
        """DFS 检测 DAG 环。悬空引用（源不在图中）由校验层报告，这里跳过。"""
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {n: WHITE for n in self.nodes()}

        def dfs(n):
            color[n] = GRAY
            for up in self.sources_of(n):
                if up not in color:        # 悬空引用：非环
                    continue
                if color[up] == GRAY:
                    return True
                if color[up] == WHITE and dfs(up):
                    return True
            color[n] = BLACK
            return False

        for n in self.nodes():
            if color[n] == WHITE and dfs(n):
                return True
        return False

    # ---------- 序列化 ----------
    def to_api(self) -> dict:
        return {k: {"class_type": v.get("class_type"),
                    "inputs": dict(v.get("inputs", {})),
                    **({"_meta": v["_meta"]} if "_meta" in v else {})}
                for k, v in self.api.items()}

    def summary(self) -> str:
        """给 LLM 的紧凑状态描述。"""
        lines = [f"工作流共 {len(self.nodes())} 个节点："]
        for nid in self.nodes():
            cls = self.class_of(nid)
            links = {k: f"<-{parse_link(v)}" for k, v in
                     self.api[nid].get("inputs", {}).items()
                     if parse_link(v)}
            scalars = {k: (repr(v)[:24]) for k, v in
                       self.api[nid].get("inputs", {}).items()
                       if not parse_link(v)}
            lines.append(f"  #{nid} {cls}"
                         f"{'  连线:' + str(links) if links else ''}"
                         f"{'  参数:' + str(scalars) if scalars else ''}")
        return "\n".join(lines)


def from_api(api: dict, knowledge) -> Graph:
    return Graph(api, knowledge)
