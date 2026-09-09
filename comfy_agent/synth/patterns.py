# -*- coding: utf-8 -*-
"""高频插入模式：把常见需求变成一键接线。

- add_lora：checkpoint 与消费方之间插入 LoraLoaderModelOnly
  （ckpt 槽0 MODEL 输出 -> LoRA.model，LoRA 槽0 -> 原消费方）
- add_controlnet：在正向/负向条件与采样器之间插入 ControlNet 链
  （Canny 预处理离线安全；union_sdxl_promax 本机已有）
"""
from __future__ import annotations

from .graph import Graph
from .ops import EditError, add_node, connect, disconnect

SDXL_UNION_CN = "controlnet++_union_sdxl_promax.safetensors"


def add_lora(g: Graph, lora_name: str, strength: float = 1.0,
             ckpt_node: str = None, target_model_input: tuple = None) -> str:
    """插入 LoRA：找出 checkpoint 节点的 MODEL 输出（槽0），在其所有
    消费方前插入 LoraLoaderModelOnly。返回新节点号。"""
    ckpt_node = ckpt_node or _find_class(g, "CheckpointLoaderSimple")
    if ckpt_node is None:
        raise EditError("图中没有 CheckpointLoaderSimple 节点",
                        {"reason": "no_checkpoint"})
    # 所有直接消费 ckpt[0] 的连线
    consumers = [(dst, name, link.slot) for dst, name, link in
                 g.links_from(ckpt_node) if link.slot == 0]
    if not consumers:
        raise EditError("checkpoint 的 MODEL 输出没有被消费",
                        {"reason": "no_consumer"})
    new_id = _fresh_id(g)
    add_node(g, new_id, "LoraLoaderModelOnly", inputs={
        "lora_name": lora_name, "strength_model": strength}, auto_defaults=True)
    connect(g, ckpt_node, 0, new_id, "model")
    for dst, name, _ in consumers:
        disconnect(g, dst, name)
        connect(g, new_id, 0, dst, name)
    return new_id


def add_controlnet(g: Graph, image_node: str, controlnet_name: str = None,
                   strength: float = 0.8, low_threshold: float = 0.4,
                   high_threshold: float = 0.8) -> list[str]:
    """在正向/负向条件与采样器之间插入 Canny ControlNet 链。

    步骤：加 Canny 预处理器（输入图）、ControlNetLoader、
    ControlNetApplyAdvanced（正/负条件重接）。
    返回新增节点号列表。"""
    cn_name = controlnet_name or SDXL_UNION_CN
    pos_enc = _find_class(g, "CLIPTextEncode", nth=0)
    neg_enc = _find_class(g, "CLIPTextEncode", nth=1)
    samplers = _find_samplers(g)
    if pos_enc is None or neg_enc is None or not samplers:
        raise EditError("图中缺少 CLIPTextEncode×2 或采样器节点",
                        {"reason": "no_anchor",
                         "hint": "需要 文生图/图生图 结构的模板"})

    n_canny = _fresh_id(g)
    n_cn = _fresh_id(g, n_canny)
    n_apply = _fresh_id(g, n_canny, n_cn)

    add_node(g, n_canny, "Canny", inputs={
        "image": [image_node, 0],
        "low_threshold": low_threshold,
        "high_threshold": high_threshold}, auto_defaults=True)
    add_node(g, n_cn, "ControlNetLoader",
             inputs={"control_net_name": cn_name}, auto_defaults=True)
    # ControlNetApplyAdvanced：输入正/负条件，输出槽0=正、1=负
    add_node(g, n_apply, "ControlNetApplyAdvanced", inputs={
        "strength": strength, "start_percent": 0.0, "end_percent": 1.0,
        "control_net": [n_cn, 0], "image": [n_canny, 0]}, auto_defaults=True)

    # 重接：pos_enc -> n_apply.positive；neg_enc -> n_apply.negative；
    # n_apply[0] -> sampler.positive；n_apply[1] -> sampler.negative
    connect(g, pos_enc, 0, n_apply, "positive")
    connect(g, neg_enc, 0, n_apply, "negative")
    for s in samplers:
        if g.link_at(s, "positive") and \
                g.link_at(s, "positive").src == pos_enc:
            disconnect(g, s, "positive")
            connect(g, n_apply, 0, s, "positive")
        if g.link_at(s, "negative") and \
                g.link_at(s, "negative").src == neg_enc:
            disconnect(g, s, "negative")
            connect(g, n_apply, 1, s, "negative")
    return [n_canny, n_cn, n_apply]


# ---------- 工具 ----------

def _find_class(g: Graph, cls: str, nth: int = 0):
    hits = [n for n in g.nodes() if g.class_of(n) == cls]
    return hits[nth] if nth < len(hits) else None


def _find_samplers(g: Graph) -> list[str]:
    return [n for n in g.nodes() if g.class_of(n) in
            ("KSampler", "KSamplerAdvanced")]


def _fresh_id(g: Graph, *reserved: str) -> str:
    used = set(g.nodes()) | set(reserved)
    i = 100
    while str(i) in used:
        i += 1
    return str(i)
