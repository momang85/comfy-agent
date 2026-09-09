# -*- coding: utf-8 -*-
"""跨设备模型适配：模板默认模型在本机不存在时，自动绑定本机同家族模型。

背景：模板里记录的模型名（novaAnimeXL/anything-v5 等）来自开发机，
他人设备模型库不同。图像模板（SDXL/SD1.5 checkpoint）可在运行期
自动适配本机任意 checkpoint；视频模板（minimax/ltx 专属文件）不参与
适配，缺什么仍按缺什么报错。

适配规则：
- 用户显式传了 ckpt 且本机存在 -> 尊重用户，不动
- 用户显式传的 ckpt 本机不存在 -> 仅同家族替换（不违背用户意图）
- 默认 ckpt 本机不存在 -> 自动选：model_prefs 偏好 > 同家族 > 任意可用
"""
from __future__ import annotations

from . import config

ADAPTABLE_FAMILIES = ("sdxl", "sd15")


def pick_local_checkpoint(family: str, knowledge, prefs: dict = None) -> str | None:
    """从本机 checkpoints 选一个模型。

    优先级：model_prefs 中该家族的偏好 -> 同家族（文件名家族标记）
    -> 另一家族的偏好 -> 任意 checkpoint 保底。
    返回 ComfyUI 模型清单中的原文件名（含可能存在的子目录段）。"""
    cps = [str(c) for c in knowledge.all_checkpoints()]
    if not cps:
        return None
    prefs = prefs or {}
    other = "sd15" if family == "sdxl" else "sdxl"

    def norm(s: str) -> str:
        return s.lower().replace("\\", "/").strip()

    wanted_same = norm(prefs.get(family, ""))
    wanted_other = norm(prefs.get(other, ""))

    def first_hit(wanted: str) -> str | None:
        if not wanted:
            return None
        for c in cps:
            cn = norm(c)
            if cn == wanted or wanted in cn or cn in wanted:
                return c
        return None

    hit = first_hit(wanted_same)
    if hit:
        return hit
    same_family = [c for c in cps if config.family_of(c) == family]
    if same_family:
        return same_family[0]
    hit = first_hit(wanted_other)
    if hit:
        return hit
    return cps[0]


def checkpoint_exists(knowledge, name: str) -> bool:
    """checkpoint 是否存在于本机模型清单（ckpt_name 输入只认 checkpoints 目录）。"""
    return bool(knowledge.find_model(name, folders=["checkpoints"]))


def adapt_ckpt(tpl, params: dict, knowledge) -> tuple[dict, list[str]]:
    """运行期绑定 checkpoint。返回 (新参数, 适配说明列表)。"""
    ckpt_param = next((p for p in tpl.params() if p.name == "ckpt"), None)
    if ckpt_param is None:
        return dict(params), []           # 视频等无 ckpt 参数的模板不参与
    params = dict(params)
    default = ckpt_param.default or ""
    explicit = bool(params.get("ckpt"))
    current = params.get("ckpt") or default
    if not current or checkpoint_exists(knowledge, current):
        return params, []
    fam = config.family_of(current)
    if fam not in ADAPTABLE_FAMILIES:
        return params, []
    prefs = config.load_user_settings().get("model_prefs") or {}
    pick = pick_local_checkpoint(fam, knowledge, prefs)
    if pick is None:
        return params, []
    if explicit and config.family_of(pick) != fam:
        # 显式指定的模型缺失：跨家族替换违背用户意图，宁可报缺
        return params, []
    params["ckpt"] = pick
    notes = [f"模型 {current!r} 本机不存在，已自动适配为本机模型 {pick!r}"
             f"（{config.family_of(pick)} 家族）"]
    # 默认模型缺失的兜底场景：SDXL 模板落到 SD1.5 模型时，
    # 把默认分辨率收敛到 512（SD1.5 在 1024 上低效易畸变）
    if not explicit and config.family_of(pick) == "sd15" and fam == "sdxl":
        for k in ("width", "height"):
            if k not in params and any(p.name == k for p in tpl.params()):
                params[k] = 512
    return params, notes
