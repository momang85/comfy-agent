# -*- coding: utf-8 -*-
"""本地预校验：提交前对照 object_info 检查工作流（省钱省时间的守门员）。

检查项：
  1. 节点类存在
  2. 必填输入齐全（required 全部出现，optional 不管）
  3. 枚举值合法（COMBO 选项）
  4. 数值范围（min/max）
  5. 文件型输入存在（模型/图片等，对照本机模型清单）
  6. 连线引用存在且类型兼容（宽松：只查引用目标存在）
"""
from __future__ import annotations

from typing import Optional

from .knowledge import Knowledge

FILE_SUFFIXES = (".safetensors", ".pt", ".ckpt", ".gguf", ".pth", ".sft",
                 ".png", ".jpg", ".jpeg", ".webp", ".bmp")


class ValidationIssue:
    def __init__(self, node_id: str, node_class: str, input_name: Optional[str],
                 kind: str, message: str, suggestion=None):
        self.node_id = node_id
        self.node_class = node_class
        self.input_name = input_name
        self.kind = kind          # missing_node | missing_input | bad_enum |
                                  # out_of_range | missing_file | bad_link | type_mismatch
        self.message = message
        self.suggestion = suggestion   # 修复建议（repair.py 可直接采用）

    def to_dict(self):
        return {"node": self.node_id, "class": self.node_class,
                "input": self.input_name, "kind": self.kind,
                "message": self.message, "suggestion": self.suggestion}

    def __repr__(self):
        return f"[{self.kind}] {self.node_class}(#{self.node_id})" \
               f"{('.' + self.input_name) if self.input_name else ''}: {self.message}"


def validate_workflow(api: dict, knowledge: Knowledge) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for node_id, node in api.items():
        cls = node.get("class_type", "")
        inputs = node.get("inputs", {})
        info = knowledge.node_info(cls)
        if info is None:
            cands = [r["class"] for r in knowledge.find_nodes(cls, limit=3)]
            issues.append(ValidationIssue(
                node_id, cls, None, "missing_node",
                f"节点 {cls} 在本机不存在",
                suggestion={"rename": cands[0]} if cands else None))
            continue

        spec_all = dict(info.get("input", {}).get("required", {}))
        spec_all.update(info.get("input", {}).get("optional", {}))

        # 动态输入（COMFY_AUTOGROW_V3）：object_info 里是模板名（如 images），
        # 服务器实际接受 输入名.前缀+序号 命名（images.image0）——
        # 模板名本身与序号形式都不算缺
        dyn_prefixes = []
        for name, spec in list(spec_all.items()):
            if _is_autogrow(spec):
                tmpl = (spec[1] or {}).get("template", {})
                dyn_prefixes.append((name, tmpl.get("prefix", name)))
        import re as _re

        def _is_dyn_name(n):
            return any(_re.fullmatch(p + r"\d+", n) or
                       _re.fullmatch(rf"{_re.escape(outer)}\.{_re.escape(p)}\d+", n)
                       for outer, p in dyn_prefixes)

        # 必填齐全（动态输入的模板名无需出现，其序号形式存在即可）
        for name in info.get("input", {}).get("required", {}):
            if _is_autogrow(spec_all.get(name)):
                if not any(_is_dyn_name(n) for n in inputs):
                    issues.append(ValidationIssue(
                        node_id, cls, name, "missing_input",
                        f"缺少必填动态输入 {name}（需 {dyn_prefixes[0] if dyn_prefixes else '?'}0 等序号形式）"))
                continue
            if name not in inputs:
                issues.append(ValidationIssue(
                    node_id, cls, name, "missing_input",
                    f"缺少必填输入 {name}"))

        for name, val in inputs.items():
            spec = spec_all.get(name)
            if spec is None and _is_dyn_name(name):
                continue   # 动态输入的序号形式：合法
            if spec is None:
                # 未知输入：服务器会报错
                issues.append(ValidationIssue(
                    node_id, cls, name, "missing_input",
                    f"输入 {name} 不在节点定义中"))
                continue
            # 连线值
            if isinstance(val, list) and len(val) == 2 and isinstance(val[0], str) \
                    and val[0].isdigit():
                src_id = val[0]
                if src_id not in api:
                    issues.append(ValidationIssue(
                        node_id, cls, name, "bad_link",
                        f"连线引用的节点 {src_id} 不存在"))
                continue

            # 标量校验
            t = spec[0] if spec else None
            opts = spec[1] if isinstance(spec, list) and len(spec) >= 2 \
                and isinstance(spec[1], dict) else {}

            if isinstance(t, list):    # 枚举 [[choices], opts]
                choices = [str(c) for c in t]
                if str(val) not in choices:
                    # 文件名枚举（模型选择器）：跨家族匹配毫无意义
                    # （如把丢失的 SDXL VAE 换成 minimax 音频 VAE），
                    # 必须同家族才给建议；跨家族不匹配时留空交给结构级修复。
                    # 图片输入不自动替换（pasted/xx 是用户素材占位，运行时上传）。
                    is_image_input = name.lower() == "image" or cls in (
                        "LoadImage", "LoadImageMask", "LoadImageOutput")
                    if is_image_input:
                        suggestion = None
                    elif any(c.endswith(FILE_SUFFIXES) for c in choices[:3]):
                        suggestion = {"enum": _closest_same_family(str(val), choices)}
                    else:
                        suggestion = {"enum": _closest(str(val), choices)}
                    issues.append(ValidationIssue(
                        node_id, cls, name, "bad_enum",
                        f"{name}={val!r} 不在合法选项中（共{len(choices)}项）",
                        suggestion=suggestion))
            elif t == "INT" and isinstance(val, (int, float)):
                if "min" in opts and val < opts["min"] or "max" in opts and val > opts["max"]:
                    clamped = _clamp(val, opts.get("min"), opts.get("max"))
                    issues.append(ValidationIssue(
                        node_id, cls, name, "out_of_range",
                        f"{name}={val} 超出范围 [{opts.get('min')}, {opts.get('max')}]",
                        suggestion={"value": clamped}))
            elif t == "FLOAT" and isinstance(val, (int, float)):
                if "min" in opts and val < opts["min"] or "max" in opts and val > opts["max"]:
                    clamped = _clamp(val, opts.get("min"), opts.get("max"))
                    issues.append(ValidationIssue(
                        node_id, cls, name, "out_of_range",
                        f"{name}={val} 超出范围 [{opts.get('min')}, {opts.get('max')}]",
                        suggestion={"value": clamped}))
            elif t == "STRING" and isinstance(val, str):
                # 文件型字符串：对照本机模型清单
                if val.endswith(FILE_SUFFIXES) and "/" not in val.replace("\\", "/")[:0]:
                    pass  # 文件存在性在 knowledge.find_model 兜底
                if val.endswith(FILE_SUFFIXES):
                    folder = _folder_of_input(knowledge, cls, name)
                    if folder and knowledge.models.get(folder):
                        names = [str(x).replace("\\", "/") for x in knowledge.models[folder]]
                        val_norm = val.replace("\\", "/")
                        if val_norm not in names:
                            # 图片输入不自动替换（pasted/xx 是用户素材占位，
                            # 运行时应重新上传）；模型文件才做同家族模糊匹配
                            if name.lower() == "image" or folder is None:
                                issues.append(ValidationIssue(
                                    node_id, cls, name, "missing_file",
                                    f"{name}={val!r} 不在 {folder or 'input'} 清单中（运行时上传）",
                                    suggestion=None))
                            else:
                                best = _closest_same_family(val_norm, names)
                                if best:
                                    issues.append(ValidationIssue(
                                        node_id, cls, name, "missing_file",
                                        f"{name}={val!r} 不在 {folder} 清单中",
                                        suggestion={"file": best}))

    return issues


def _is_autogrow(spec) -> bool:
    """spec 是否为 COMFY_AUTOGROW_V3 动态输入（可增删的输入组）。"""
    return (isinstance(spec, list) and spec and
            spec[0] == "COMFY_AUTOGROW_V3")


def _folder_of_input(knowledge: Knowledge, cls: str, input_name: str) -> Optional[str]:
    """猜测文件型输入对应的模型目录（按目录名与输入名的相关性）。"""
    name_l = input_name.lower()
    mapping = {
        "ckpt": "checkpoints", "checkpoint": "checkpoints",
        "lora": "loras", "vae": "vae", "unet": "unet", "model": "diffusion_models",
        "upscale": "upscale_models", "control": "controlnet",
        "image": None, "clip": "clip",
    }
    for key, folder in mapping.items():
        if key in name_l and folder:
            if folder in knowledge.models or folder == "clip":
                real = "clip" if folder == "clip" and "clip" not in knowledge.models and \
                    "text_encoders" in knowledge.models else folder
                return real
    return None


def _closest_same_family(val: str, choices: list[str]) -> Optional[str]:
    """同家族模糊匹配：VAE 只匹配 VAE 类、LoRA 只匹配 LoRA 类等。
    防止把丢失的 SDXL VAE '修复'成 minimax 音频 VAE 这类跨家族错配。
    家族判定：文件名中的关键词必须与候选共享（sdxl/sd15/ltx/minimax 等），
    或候选与原文件语义同名（sdxlVAE -> sdxl_vae）。"""
    from difflib import SequenceMatcher
    fam_keywords = ("sdxl", "sd15", "sd1.5", "ltx", "minimax", "wan", "flux",
                    "anything", "nova", "qwen", "gemma")
    val_fams = {f for f in fam_keywords if f in val.lower()}
    best, best_score = None, 0.0
    for c in choices:
        c_fams = {f for f in fam_keywords if f in c.lower()}
        # 家族不相交且双方都有家族标记 -> 跳过（跨家族不匹配）
        if val_fams and c_fams and not (val_fams & c_fams):
            continue
        s = SequenceMatcher(None, val.lower(), c.lower()).ratio()
        if s > best_score:
            best, best_score = c, s
    return best if best_score > 0.4 else None


def _closest(val: str, choices: list[str]) -> Optional[str]:
    from difflib import SequenceMatcher
    best, best_score = None, 0.0
    for c in choices:
        s = SequenceMatcher(None, val.lower(), c.lower()).ratio()
        if s > best_score:
            best, best_score = c, s
    return best if best_score > 0.4 else (choices[0] if choices else None)


def _clamp(val, lo, hi):
    if lo is not None and val < lo:
        return lo
    if hi is not None and val > hi:
        return hi
    return val
