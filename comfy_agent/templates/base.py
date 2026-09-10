# -*- coding: utf-8 -*-
"""模板基类：参数 schema、模型绑定、渲染为 API 格式工作流。

设计要点：
- 模板 = 已验证的 API 格式工作流骨架 + 可调参数声明
- render(params) 把参数注入骨架（只改参数不动结构——ComfyMind 局部回溯路线）
- 参数声明带类型/默认值/范围/中文说明，供大脑和 CLI 使用
"""
from __future__ import annotations

from typing import Any, Optional


class Param:
    def __init__(self, name: str, ptype: str, default, title: str = "",
                 choices: list = None, minv=None, maxv=None,
                 required: bool = False, desc: str = "",
                 unit: str = "", aliases: list = None, recommended=None):
        self.name = name
        self.ptype = ptype          # str | int | float | bool | image | choice
        self.default = default
        self.title = title or name
        self.choices = choices
        self.minv = minv
        self.maxv = maxv
        self.required = required
        self.desc = desc
        self.unit = unit            # 单位说明（如"帧(24fps)"），消除量纲歧义
        self.aliases = aliases or []   # 大脑可能用的别名（帧/秒、duration 等）
        self.recommended = recommended if recommended is not None else default

    def to_dict(self):
        d = {"name": self.name, "type": self.ptype, "default": self.default,
             "title": self.title}
        if self.choices:
            d["choices"] = self.choices
        if self.minv is not None:
            d["min"] = self.minv
        if self.maxv is not None:
            d["max"] = self.maxv
        if self.unit:
            d["unit"] = self.unit
        if self.recommended is not None and self.recommended != self.default:
            d["recommended"] = self.recommended
        if self.desc:
            d["desc"] = self.desc
        return d


class Template:
    """一个模板 = 工作流骨架 + 参数声明 + 注入规则。"""
    id: str = ""                    # 英文标识（CLI 用）
    name: str = ""                  # 中文名
    category: str = ""              # image | video | upscale
    family: str = ""                # 提示词规范家族: sdxl | sd15 | ltx | minimax
    desc: str = ""                  # 一句话描述（大脑选模板用）
    models_used: list[str] = []     # 依赖的本机模型文件
    est_vram_gb: float = 6.0        # 预估显存
    est_minutes: str = "1-3"        # 预估耗时

    def params(self) -> list[Param]:
        raise NotImplementedError

    def render(self, p: dict) -> dict:
        """参数 -> API 格式工作流。子类实现。"""
        raise NotImplementedError

    # ---- 工具 ----
    @staticmethod
    def _fill_defaults(p: dict, params: list[Param]) -> dict:
        out = {}
        for prm in params:
            out[prm.name] = p.get(prm.name, prm.default)
        return out

    def normalize_params(self, p: dict) -> tuple[dict, list[str]]:
        """参数规范化：别名 → 主名（帧/秒、duration 等歧义名收敛）。

        返回 (规范化后的参数, 说明列表)。子类可覆盖做单位换算。"""
        out, notes = dict(p or {}), []
        for prm in self.params():
            for al in prm.aliases:
                if al not in out:
                    continue
                if prm.name not in out:
                    out[prm.name] = out.pop(al)
                    notes.append(f"参数 {al} 已按别名映射为 {prm.name}")
                else:
                    out.pop(al)      # 主名已给出：丢弃别名键，避免"未识别参数"告警
        return out, notes

    def to_dict(self):
        return {"id": self.id, "name": self.name, "category": self.category,
                "family": self.family, "desc": self.desc,
                "models": self.models_used,
                "vram_gb": self.est_vram_gb, "minutes": self.est_minutes,
                "params": [prm.to_dict() for prm in self.params()]}

    def missing_models(self, knowledge) -> list[str]:
        """检查本机是否有模板所需模型。"""
        missing = []
        for m in self.models_used:
            hits = knowledge.find_model(m, folders=_folders_for(m))
            if not hits:
                missing.append(m)
        return missing


def _folders_for(model_name: str) -> list[str]:
    n = model_name.lower()
    if "lora" in n:
        return ["loras"]
    if "vae" in n:
        return ["vae"]
    if "qwen3vl" in n or "gemma" in n:
        return ["text_encoders"]
    if "fl2va" in n or "minimax" in n:
        return ["diffusion_models", "unet"]
    return None   # 全部文件夹搜索


# ---- 参数注入辅助：通用节点构造 ----

def ksampler(model, positive, negative, latent, seed=0, steps=20, cfg=7.0,
             sampler="euler", scheduler="simple", denoise=1.0):
    return {"class_type": "KSampler", "inputs": {
        "model": model, "positive": positive, "negative": negative,
        "latent_image": latent, "seed": seed, "steps": steps, "cfg": cfg,
        "sampler_name": sampler, "scheduler": scheduler, "denoise": denoise}}


def clip_text_encode(clip, text):
    return {"class_type": "CLIPTextEncode", "inputs": {"clip": clip, "text": text}}
