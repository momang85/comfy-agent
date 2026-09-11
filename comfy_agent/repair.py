# -*- coding: utf-8 -*-
"""修复层：验证问题/服务器报错 -> 参数级修复 -> 重试（≤3次）。

设计对应报告的"执行-修复闭环"空白点：
  - 本地校验问题（validate.py 产出的 ValidationIssue）
  - 服务器 /prompt 校验失败（node_errors 结构）
  - 执行期错误（OOM / 形状不匹配，来自 /history status）

修复原则（ComfyMind 局部回溯路线的工程化）：
  参数级优先，不动 DAG 结构；无法自动修复时给出明确中文诊断。
"""
from __future__ import annotations

import re
from typing import Optional

from .knowledge import Knowledge
from .validate import validate_workflow, ValidationIssue


class RepairReport:
    def __init__(self):
        self.fixes: list[dict] = []      # 已应用的修复
        self.unfixable: list[dict] = []  # 无法自动修复的问题（含诊断）

    @property
    def ok(self) -> bool:
        return not self.unfixable

    def to_dict(self):
        return {"fixes": self.fixes, "unfixable": self.unfixable,
                "ok": self.ok}


def auto_repair(api: dict, knowledge: Knowledge,
                issues: list[ValidationIssue] = None) -> tuple[dict, RepairReport]:
    """按本地校验问题自动修复工作流（原地修改的拷贝）。"""
    report = RepairReport()
    wf = {nid: {"class_type": n["class_type"],
                "inputs": dict(n.get("inputs", {})),
                **({"_meta": n["_meta"]} if "_meta" in n else {})}
           for nid, n in api.items()}

    if issues is None:
        issues = validate_workflow(wf, knowledge)

    for iss in issues:
        node = wf.get(iss.node_id)
        if node is None:
            report.unfixable.append(iss.to_dict())
            continue
        fixed = _fix_issue(node, iss, knowledge, wf)
        if fixed:
            report.fixes.append({"node": iss.node_id, "class": iss.node_class,
                                 "input": iss.input_name,
                                 "was": iss.to_dict().get("message"),
                                 "now": fixed})
        else:
            # 图片占位符（pasted/xx 等）是运行时输入，不算阻断性失败
            if not (iss.input_name and iss.input_name.lower() == "image"
                    and iss.kind in ("bad_enum", "missing_file")):
                report.unfixable.append(iss.to_dict())

    # 修复后再校验一轮（修复可能引入新问题；图片未上传属运行期输入不算阻断）
    seen_keys = set()
    remaining = validate_workflow(wf, knowledge)
    for iss in remaining:
        if iss.kind == "missing_file" and iss.suggestion is None:
            continue   # 无建议的缺失文件（运行时上传类）不阻断
        if iss.input_name and iss.input_name.lower() == "image" and \
                iss.kind in ("bad_enum", "missing_file"):
            continue   # 图片占位符：运行时由用户上传替换，不算阻断
        key = (iss.node_id, iss.kind, iss.input_name, iss.message)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        report.unfixable.append(iss.to_dict())

    return wf, report


# CheckpointLoaderSimple 输出槽位: 0=MODEL, 1=CLIP, 2=VAE
_CKPT_VAE_SLOT = 2


def _swap_vae_to_checkpoint(wf: dict, vae_node_id: str) -> bool:
    """结构级修复：删除失效 VAELoader，改用 checkpoint 自带 VAE（槽2）。"""
    vae_ref = [str(vae_node_id), 0]
    ckpt_id = _find_checkpoint(wf, None)
    if ckpt_id is None:
        return False
    rewired = False
    for nid, node in wf.items():
        for name, val in list(node.get("inputs", {}).items()):
            if val == vae_ref:
                node["inputs"][name] = [str(ckpt_id), _CKPT_VAE_SLOT]
                rewired = True
    if rewired:
        wf.pop(str(vae_node_id), None)
    return rewired


def _fix_issue(node: dict, iss: ValidationIssue, knowledge: Knowledge,
               wf: dict = None) -> Optional[dict]:
    """尝试修复单个问题，返回修复说明（None=无法修复）。"""
    s = iss.suggestion or {}
    inputs = node["inputs"]

    if iss.kind == "bad_enum" and s.get("enum"):
        old = inputs.get(iss.input_name)
        inputs[iss.input_name] = s["enum"]
        return {iss.input_name: f"{old!r} -> {s['enum']!r}"}

    # VAE 枚举失效且无同家族候选（suggestion 为 None）：
    # 走结构级修复——改用 checkpoint 自带 VAE
    if iss.kind == "bad_enum" and iss.input_name == "vae_name" and not s.get("enum"):
        fixed = _swap_vae_to_checkpoint(wf, iss.node_id)
        if fixed:
            return {"structure": f"删除失效 VAELoader #{iss.node_id}，"
                    "VAEDecode/Encode 改用 checkpoint 自带 VAE"}
        return None

    if iss.kind == "out_of_range" and s.get("value") is not None:
        old = inputs.get(iss.input_name)
        inputs[iss.input_name] = s["value"]
        return {iss.input_name: f"{old} -> {s['value']}"}

    if iss.kind == "missing_file" and s.get("file"):
        old = inputs.get(iss.input_name)
        inputs[iss.input_name] = s["file"]
        return {iss.input_name: f"{old!r} -> {s['file']!r}（模糊匹配本机清单）"}

    if iss.kind == "missing_file" and iss.input_name == "vae_name":
        # VAE 文件丢失：不做同家族猜测（本机没有 SDXL VAE 文件），
        # 走结构级修复——改用 checkpoint 自带 VAE
        fixed = _swap_vae_to_checkpoint(wf, iss.node_id)
        if fixed:
            return {"structure": f"删除失效 VAELoader #{iss.node_id}，"
                    "VAEDecode/Encode 改用 checkpoint 自带 VAE"}
        return None

    if iss.kind == "missing_node" and s.get("rename"):
        node["class_type"] = s["rename"]
        return {"class_type": f"重命名为 {s['rename']}"}

    return None


def repair_vae_to_checkpoint(api: dict, vae_node_id: str) -> dict:
    """结构级修复：删除失效的 VAELoader，改用 checkpoint 自带 VAE。
    返回修复后的工作流（原 dict 被修改）。"""
    # 找到引用 vae_node_id 的节点
    vae_out = [str(vae_node_id), 0]
    for nid, node in api.items():
        for name, val in list(node.get("inputs", {}).items()):
            if val == vae_out:
                # 找 checkpoint loader
                ckpt_id = _find_checkpoint(api, node)
                if ckpt_id:
                    node["inputs"][name] = [str(ckpt_id), 1]   # ckpt 槽1=VAE
    api.pop(str(vae_node_id), None)
    return api


def _find_checkpoint(api: dict, near_node: dict) -> Optional[int]:
    for nid, node in api.items():
        if node.get("class_type") in ("CheckpointLoaderSimple", "CheckpointLoader"):
            return int(nid)
    return None


# ---------------- 服务器报错解析 ----------------

def parse_server_validation_error(payload: dict) -> list[dict]:
    """解析 POST /prompt 400 响应的 node_errors 结构。"""
    out = []
    errors = payload.get("error") or {}
    if isinstance(errors, dict):
        out.append({"kind": "server", "message": errors.get("message", str(errors)),
                    "type": errors.get("type")})
    for node_id, nerr in (payload.get("node_errors") or {}).items():
        for e in nerr.get("errors", []):
            out.append({"kind": "server_node", "node": node_id,
                        "input": e.get("details", "").get("input_name") if
                        isinstance(e.get("details"), dict) else None,
                        "message": e.get("message", str(e)),
                        "type": e.get("type")})
    return out


def parse_execution_error(history_entry: dict) -> Optional[dict]:
    """从 /history 条目解析执行期错误（OOM/形状不匹配等）。"""
    status = history_entry.get("status", {})
    if status.get("status_str") != "error":
        return None
    msgs = []
    for m in status.get("messages", []):
        if m and m[0] == "execution_error":
            d = m[1]
            return {"node": d.get("node_id"), "class": d.get("node_type"),
                    "message": d.get("exception_message", ""),
                    "traceback": (d.get("exception_traceback") or "")[-800:]}
    return {"message": str(status)}


def suggest_for_execution_error(err: dict, api: dict) -> Optional[dict]:
    """执行期错误的修复建议（参数级）。"""
    msg = (err.get("message") or "").lower()
    node_id = err.get("node")
    node = api.get(str(node_id)) if node_id else None

    if "out of memory" in msg or "oom" in msg or "alloc" in msg:
        suggestions = []
        # 视频节点类：EmptyLatent* 之外，视频模板用这些节点承载分辨率/帧数
        video_nodes = ("MiniMaxH3ImageToVideo", "EmptyMiniMaxH3LatentAV",
                       "LTXVImgToVideo")
        for nid, n in api.items():
            if n["class_type"] not in video_nodes:
                continue
            ins = n["inputs"]
            for k in ("width", "height"):
                v = ins.get(k)
                if isinstance(v, int) and v > 256:
                    ins[k] = max(256, int(v * 0.6) // 32 * 32)
                    suggestions.append(f"#{nid} {k} {v}->{ins[k]}（视频降分辨率）")
            ln = ins.get("length")
            if isinstance(ln, int) and ln > 32:
                ins["length"] = max(32, ln // 2)
                suggestions.append(f"#{nid} length {ln}->{ins['length']}"
                                   "（帧数减半）")
        # 降分辨率：找 EmptyLatentImage / EmptySD3LatentImage 等
        for nid, n in api.items():
            if n["class_type"] in ("EmptyLatentImage", "EmptySD3LatentImage",
                                   "EmptyLatent"):
                w = n["inputs"].get("width")
                h = n["inputs"].get("height")
                if isinstance(w, int) and w > 512:
                    n["inputs"]["width"] = max(512, w // 2)
                    suggestions.append(f"#{nid} width {w}->"
                                       f"{n['inputs']['width']}")
                if isinstance(h, int) and h > 512:
                    n["inputs"]["height"] = max(512, h // 2)
                    suggestions.append(f"#{nid} height {h}->"
                                       f"{n['inputs']['height']}")
        # 降批
        for nid, n in api.items():
            b = n["inputs"].get("batch_size")
            if isinstance(b, int) and b > 1:
                n["inputs"]["batch_size"] = max(1, b // 2)
                suggestions.append(f"#{nid} batch {b}->{n['inputs']['batch_size']}")
        if suggestions:
            return {"type": "oom", "applied": suggestions}
        return {"type": "oom", "advice": "显存不足且无可降参数：建议用默认（动态）显存"
                "模式重启 ComfyUI（运行 一键启动.bat 会自动拉起），或换更小的模型"}

    if "shape" in msg or "size mismatch" in msg:
        if node:
            # 常见：EmptyLatent 尺寸与模型不匹配 -> 对齐到 64/32 倍数
            for k in ("width", "height"):
                v = node["inputs"].get(k)
                if isinstance(v, int):
                    aligned = max(64, round(v / 64) * 64)
                    if aligned != v:
                        node["inputs"][k] = aligned
                        return {"type": "shape", "applied":
                                [f"#{node_id} {k} {v}->{aligned}（64对齐）"]}
        return {"type": "shape", "advice": "张量形状不匹配：检查模型与latent/VAE组合"}

    return None


def friendly_error_zh(err: dict) -> str:
    """把技术错误翻译成用户能懂的中文。"""
    msg = (err.get("message") or "")
    low = msg.lower()
    if "out of memory" in low:
        return "显存不足（OOM）。已尝试自动降低分辨率/批量；若仍失败请用低显存模式重启 ComfyUI。"
    if "shape" in low:
        return "生成尺寸与模型不匹配，已自动对齐到兼容尺寸。"
    if "return types" in low or "mismatched" in low:
        return "节点连线类型不匹配，请检查工作流结构。"
    if "locate the file on the hub" in low or "huggingface" in low or \
            "cannot find the requested files" in low:
        return ("预处理器模型缺失：节点需要从 HuggingFace 下载模型但网络不可达。"
                "建议换用不依赖外部下载的模板（如 i2i 而非 ControlNet 类模板）。")
    return f"执行出错：{msg[:200]}"
