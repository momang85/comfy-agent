# -*- coding: utf-8 -*-
"""UI 格式 -> API 格式转换器（工作流 agent 化的核心基建）。

把画布上保存的工作流（nodes/links/widgets_values，给人看的）
转成 /prompt 端点需要的 API 格式（{node_id: {class_type, inputs}}，给机器执行的）。

规则要点：
- widgets_values 按节点 input 定义顺序（required+optional）逐一映射为输入值
- 连线（links）转为 ["<源节点id>", <源输出槽>] 引用
- mode=4（bypass）/ mode=2（muted）的节点剔除，其连线被旁路传递
- 控件扩展值（seed 的 control_after_generate 等）按占位跳过
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .knowledge import Knowledge


class ConversionError(Exception):
    pass


# 画布原语节点（不在 object_info，转换时透明传递连线）
CANVAS_PRIMITIVES = {"Reroute", "Note", "MarkdownNote", "PrimitiveNode"}


def convert_ui_to_api(ui: dict, knowledge: Knowledge, strict: bool = True,
                      skipped: list = None) -> dict:
    """把 UI 格式工作流字典转为 API 格式。

    strict=False（语料预处理用）：本机不存在的节点跳过不转换（记录到
    skipped），保留其余节点的结构——让含少量未知节点的外部工作流也能
    贡献接线证据。"""
    nodes = {n["id"]: n for n in ui.get("nodes", [])}
    links = {l[0]: l for l in ui.get("links", [])}   # link_id -> [id, src, slot, dst, dslot, type]

    # 1) 分拣：活跃节点 / 画布原语（透明传递）/ 剔除 bypass
    active: dict[int, dict] = {}
    for nid, node in nodes.items():
        if node.get("type") in CANVAS_PRIMITIVES:
            continue
        mode = node.get("mode", 0)
        if mode in (2, 4):
            continue
        if not knowledge.has_node(node.get("type", "")):
            if strict:
                hint = _rename_hint(knowledge, node.get("type", ""))
                raise ConversionError(
                    f"节点 {node.get('type')}（id={nid}）在本机不存在。{hint}")
            if skipped is not None:
                skipped.append(node.get("type", ""))
            continue
        active[nid] = node

    def resolve_source(src_id: int, seen: frozenset = frozenset()) -> tuple[int, int] | None:
        """解析连线真实源头：穿过 Reroute / bypass 节点，返回 (活跃节点id, 输出槽)。
        Reroute：入口连线即出口（保持源槽不变）。
        bypass：保守近似——沿第一个可用输入向上（多数是 LoRA/ControlNet 直通链）。"""
        if src_id in seen:
            return None
        node = nodes.get(src_id)
        if node is None:
            return None
        if src_id in active:
            return (src_id, None)   # 槽由调用方补（resolve 时不重映射活跃节点）
        if node.get("type") == "Reroute":
            for inp in node.get("inputs", []) or []:
                link_id = inp.get("link")
                if link_id is not None and link_id in links:
                    _l, up, _s, *_ = links[link_id]
                    got = resolve_source(up, seen | {src_id})
                    if got is not None:
                        return got
            return None
        # bypass 节点：向上找活跃祖先，槽位近似为 0
        for inp in node.get("inputs", []) or []:
            link_id = inp.get("link")
            if link_id is None or link_id not in links:
                continue
            _l, up, _s, *_ = links[link_id]
            got = resolve_source(up, seen | {src_id})
            if got is not None:
                nid_active = got[0]
                return (nid_active, 0)   # bypass 直通：取上游槽 0 的保守近似
        return None

    api: dict[str, dict] = {}

    for nid, node in active.items():
        cls = node["type"]
        inputs: dict[str, Any] = {}
        # 被连线占用的输入名（link=None 的 inputs 数组项是未连线的 widget 占位，
        # 不算占用）
        linked_names: set[str] = set()

        # 2) 连线输入
        for inp in node.get("inputs", []) or []:
            name = inp["name"]
            link_id = inp.get("link")
            if link_id is None:
                continue
            linked_names.add(name)
            if link_id not in links:
                raise ConversionError(f"节点 {cls}(id={nid}) 输入 {name} 的连线缺失")
            _lid, src_id, src_slot, _dst_id, _dst_slot, _type = links[link_id]
            if src_id in active:
                inputs[name] = [str(src_id), src_slot]
                continue
            # 源是 Reroute/bypass：解析真实源
            got = resolve_source(src_id)
            if got is None:
                if strict:
                    raise ConversionError(
                        f"节点 {cls}(id={nid}) 输入 {name} 的上游 {src_id} 无法解析")
                continue   # 宽容模式：上游不存在（未安装节点），跳过该连线
            real_src, real_slot = got
            inputs[name] = [str(real_src), real_slot if real_slot is not None else src_slot]

        # 3) widget 值：按 input 定义顺序，只分配给标量型输入
        #    （INT/FLOAT/STRING/COMBO 枚举；连线型 MODEL/CONDITIONING 等不吃 widget 值）。
        #    spec 带 control_after_generate=True 的输入，其 widget 值是 UI 伴随控件
        #    （'randomize'/'fixed'），跳过一个槽位。
        widgets = node.get("widgets_values")
        if widgets is not None:
            order = knowledge.input_order(cls)
            wi = 0
            for name in order:
                if name in linked_names:
                    continue
                spec = _input_spec(knowledge, cls, name)
                if spec is None or not _is_scalar(spec):
                    continue
                if not _is_scalar(spec):
                    continue
                if wi >= len(widgets):
                    break
                inputs[name] = _coerce(spec, widgets[wi])
                wi += 1
                # ComfyUI 前端为 seed 类 widget 附加 'control_after_generate'
                # 伴随控件（fixed/increment/decrement/randomize），存在于
                # widgets_values 序列但不属于 API 输入——按保留词识别并跳过。
                # 注意：不依赖 spec 声明（UltimateSDUpscale 等节点未标记）。
                if wi < len(widgets) and isinstance(widgets[wi], str) and \
                        widgets[wi] in _CONTROL_WORDS:
                    wi += 1

        api[str(nid)] = {"class_type": cls, "inputs": inputs,
                         "_meta": {"title": node.get("title") or cls}}

    return api


def _rename_hint(knowledge: Knowledge, missing: str) -> str:
    """节点缺失时给出相似节点名建议（应对节点包升级改名）。"""
    cands = [r["class"] for r in knowledge.find_nodes(missing, limit=3)]
    if cands:
        return f"相似节点: {', '.join(cands)}（可能节点包版本更新后改名）"
    return f"所属包: {knowledge.package_of(missing)}，可用 ComfyUI-Manager 安装"


def _input_spec(knowledge: Knowledge, cls: str, name: str):
    info = knowledge.node_info(cls) or {}
    for section in ("required", "optional"):
        spec = info.get("input", {}).get(section, {}).get(name)
        if spec is not None:
            return spec
    return None


def _is_companion(spec) -> bool:
    """spec 带 control_after_generate=True 的输入，其 widget 值是 UI 伴随控件（如 'randomize'）。"""
    return (isinstance(spec, list) and len(spec) >= 2 and isinstance(spec[1], dict)
            and spec[1].get("control_after_generate") is True)


# ComfyUI 前端 seed 控件的伴随值保留词（widgets_values 中出现即跳过）
_CONTROL_WORDS = ("fixed", "increment", "decrement", "randomize")


# 标量 widget 类型：值来自 widgets_values 而非连线
_SCALAR_TYPES = {"INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"}
# 常见连线数据类型（出现即说明该输入靠连线而非 widget）
_LINK_TYPES = {"MODEL", "CLIP", "VAE", "CONDITIONING", "LATENT", "IMAGE", "MASK",
               "CONTROL_NET", "GUIDER", "NOISE", "SAMPLER", "SIGMAS", "AUDIO"}


def _is_scalar(spec) -> bool:
    """spec 是否为标量型（值来自 widgets_values）。
    形态：'INT'/'FLOAT'/'STRING'/'BOOLEAN'/['COMBO', opts]/[[choices], opts]。"""
    if not (isinstance(spec, list) and spec):
        return False
    t = spec[0]
    if isinstance(t, list):
        return True                                    # [[choices], opts] 枚举
    return isinstance(t, str) and (t in _SCALAR_TYPES or t not in _LINK_TYPES and not t.isupper())


def _coerce(spec, val):
    """把 widget 值按 spec 规整（combo 枚举/数值范围在 validate 层做，这里只做类型容错）。"""
    if spec and isinstance(spec, list) and spec:
        t = spec[0]
        if t == "INT" and isinstance(val, float):
            return int(val)
        if t == "FLOAT" and isinstance(val, (int, str)):
            try:
                return float(val)
            except (TypeError, ValueError):
                return val
    return val


def convert_file(path: str | Path, knowledge: Knowledge, strict: bool = True,
                 skipped: list = None) -> dict:
    ui = json.loads(Path(path).read_text(encoding="utf-8"))
    return convert_ui_to_api(ui, knowledge, strict=strict, skipped=skipped)
