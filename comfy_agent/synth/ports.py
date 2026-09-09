# -*- coding: utf-8 -*-
"""模板端口检测：把一个 API 格式工作流归类出输入/输出端口，供管线拼接。

- output ports：产出型节点的上游 IMAGE 源（SaveImage/PreviewImage/SaveVideo
  的 images 输入来源），或无下游的 IMAGE 产出节点
- input ports：LoadImage 类节点（image 输入）或空 latent 类节点
"""
from __future__ import annotations

from .graph import Graph, parse_link

_SAVE_TYPES = ("SaveImage", "PreviewImage", "SaveImageWebsocket", "SaveVideo")
_LOAD_TYPES = ("LoadImage", "LoadImageMask", "LoadImageOutput")


def detect_ports(api: dict, knowledge) -> dict:
    """返回 {inputs: {TYPE: [(node, input_name)]}, outputs: {TYPE: [(node, slot)]}}"""
    g = Graph(api, knowledge)
    inputs: dict[str, list] = {}
    outputs: dict[str, list] = {}

    for nid in g.nodes():
        cls = g.class_of(nid) or ""
        # 输入端口：图片加载 / 空 latent
        if cls in _LOAD_TYPES:
            inputs.setdefault("IMAGE", []).append((nid, "image"))
        elif cls in ("EmptyLatentImage", "EmptySD3LatentImage",
                     "EmptySDXLLatentImage", "EmptyLatent"):
            inputs.setdefault("LATENT", []).append((nid, None))
        # 输出端口：保存节点的上游
        if cls in _SAVE_TYPES:
            for name, val in g.api[nid].get("inputs", {}).items():
                link = parse_link(val)
                if link and link.src in g.api:
                    slot_type = g.output_type(link.src, link.slot)
                    if slot_type:
                        outputs.setdefault(slot_type, []).append(
                            (link.src, link.slot))

    # 兜底：没有 Save 节点的裸产出（IMAGE 型无下游节点）
    if not outputs.get("IMAGE"):
        for nid in g.nodes():
            if g.class_of(nid) in _SAVE_TYPES:
                continue
            outs = g.outputs(nid)
            if "IMAGE" in outs and not g.sinks_of(nid):
                outputs.setdefault("IMAGE", []).append((nid, 0))

    return {"inputs": inputs, "outputs": outputs}


def first_port(ports: dict, ptype: str, side: str):
    lst = ports.get(side, {}).get(ptype, [])
    return lst[0] if lst else None
