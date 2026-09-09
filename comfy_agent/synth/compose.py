# -*- coding: utf-8 -*-
"""管线拼接：A 的产出端口直连 B 的入口下游（入口节点删除法）。

背景：ComfyUI 0.33 重写了 LoadImage——不再接受"连线传入 IMAGE"
（load_image 无条件把输入当文件名）。因此拼接不能"替换 LoadImage 的
输入为上游引用"，而必须：找到 B 的入口节点（LoadImage / EmptyLatent），
把它的全部下游消费方改接 A 的产出端口，然后删除入口节点。
"""
from __future__ import annotations

import copy

from .graph import Graph, parse_link
from .ports import detect_ports

_IMAGE_ENTRIES = ("LoadImage", "LoadImageMask")
_LATENT_ENTRIES = ("EmptyLatentImage", "EmptySD3LatentImage",
                   "EmptySDXLLatentImage")


class ComposeError(Exception):
    pass


def _find_entry(api_b: dict, ptype: str):
    """找 B 的入口节点。IMAGE -> LoadImage；LATENT -> EmptyLatent。"""
    entries = _IMAGE_ENTRIES if ptype == "IMAGE" else _LATENT_ENTRIES
    for nid, node in api_b.items():
        if node.get("class_type") in entries:
            return str(nid)
    return None


def compose(api_a: dict, api_b: dict, knowledge,
            link_types: list[str] = None) -> dict:
    """拼接 A→B，返回合成后的 API 工作流。"""
    ports_a = detect_ports(api_a, knowledge)
    merged = copy.deepcopy(api_a)

    # B 图加前缀重映射
    remap = {}
    for k, v in api_b.items():
        new_id = "b" + str(k)
        remap[str(k)] = new_id
        node = {"class_type": v.get("class_type"), "inputs": {},
                "_meta": v.get("_meta", {})}
        for name, val in v.get("inputs", {}).items():
            link = parse_link(val)
            if link:
                node["inputs"][name] = [remap.get(link.src, link.src),
                                        link.slot]
            else:
                node["inputs"][name] = val
        merged[new_id] = node

    for ptype in (link_types or ["IMAGE", "LATENT"]):
        out_a = ports_a["outputs"].get(ptype)
        entry = _find_entry(api_b, ptype)
        if not out_a or entry is None:
            continue
        out_node, out_slot = out_a[0]
        entry_new = remap[entry]

        # 检查 B 是否使用了入口的 MASK 槽（无法用 IMAGE 直连替代）
        mask_used = [nid for nid, node in merged.items()
                     for name, val in node.get("inputs", {}).items()
                     if parse_link(val) and parse_link(val).src == entry_new
                     and parse_link(val).slot != out_slot]
        if mask_used:
            raise ComposeError(
                f"B 使用了入口节点 {entry} 的其他输出槽（{mask_used}），"
                f"无法用 A 的 {ptype} 直连")

        # 下游消费方改接 A 的产出端口
        rewired = 0
        for nid, node in merged.items():
            for name, val in list(node.get("inputs", {}).items()):
                link = parse_link(val)
                if link and link.src == entry_new:
                    node["inputs"][name] = [out_node, out_slot]
                    rewired += 1
        if rewired == 0:
            continue   # 入口没有下游（孤岛），不删除继续找其他类型
        merged.pop(entry_new, None)

        g = Graph(merged, knowledge)
        if g.has_cycle():
            raise ComposeError("拼接后出现环（DAG 违规）")
        return g.to_api()

    avail_out = {k: v for k, v in ports_a["outputs"].items() if v}
    raise ComposeError(
        f"找不到可对接的端口：A 输出 {list(avail_out)}，"
        f"B 入口 {[n for n in api_b.values() if n.get('class_type') in _IMAGE_ENTRIES + _LATENT_ENTRIES] or '无'}")
