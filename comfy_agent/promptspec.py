# -*- coding: utf-8 -*-
"""提示词规范：按模型家族生成适配的提示词模板与默认负面词。

家族差异（来自 SD 生态共识）：
  sdxl / sd15 : CLIP 编码器，偏好逗号分隔的 booru 标签风格，英文，
                有 77 token 截断（重要信息放前面）
  ltx         : Gemma-3 自然语言，英文长句，描述镜头/运动/氛围
  minimax     : Qwen3VL-32B，自然语言，中英文皆可，描述主体+动作+镜头+音频
"""
from __future__ import annotations

FAMILY_GUIDES = {
    "sdxl": {
        "lang": "英文标签（booru 风格）",
        "style": "逗号分隔关键词，质量词在前，主体细节随后；总长控制在 ~75 词内",
        "quality_prefix": "masterpiece, best quality, highly detailed",
        "example": "masterpiece, best quality, 1girl, orange cat, astronaut helmet, "
                   "starry background, detailed fur, cinematic lighting",
    },
    "sd15": {
        "lang": "英文标签（booru 风格）",
        "style": "同 SDXL 但更短（77 token 硬截断）；anything 系动漫模型偏好动漫标签",
        "quality_prefix": "masterpiece, best quality",
        "example": "masterpiece, best quality, 1cat, space helmet, orange fur, "
                   "science fiction, detailed",
    },
    "ltx": {
        "lang": "英文自然语言",
        "style": "完整句子描述：主体、动作、镜头运动（camera）、光线氛围；"
                 "避免堆砌标签",
        "quality_prefix": "",
        "example": "An orange cat wearing an astronaut helmet floats gently in "
                   "a spacecraft cabin, camera slowly dollies in, soft sunlight "
                   "through the window, cinematic, 24fps",
    },
    "minimax": {
        "lang": "中文或英文自然语言",
        "style": "描述主体+场景+动作+镜头语言+音效（H3 支持音画同生）；"
                 "Qwen3VL 编码器理解力强，可以写复杂叙事",
        "quality_prefix": "",
        "example": "一只戴宇航员头盔的橘猫漂浮在太空舱内，爪子轻轻拨动漂浮的按钮，"
                   "镜头缓慢推近，阳光从舷窗洒入，背景有轻微的仪器嗡鸣声",
    },
}

DEFAULT_NEGATIVE = {
    "sdxl": ("(worst quality, low quality:1.4), bad anatomy, bad hands, "
             "extra limbs, missing limbs, watermark, signature, text, logo"),
    "sd15": ("(worst quality, low quality:1.4), bad anatomy, bad hands, "
             "extra digits, fewer digits, watermark, signature, text"),
    "ltx": "blurry, distorted, low quality, watermark",
    "minimax": "模糊, 变形, 低质量, 水印",
}


def guide_for(family: str) -> dict:
    return FAMILY_GUIDES.get(family, FAMILY_GUIDES["sdxl"])


def negative_for(family: str) -> str:
    return DEFAULT_NEGATIVE.get(family, DEFAULT_NEGATIVE["sdxl"])


# 各家族默认负面里"不该丢"的关键项（判断大脑的自写负面是否覆盖了默认）
_NEG_KEYS = {
    "sdxl": ("worst quality", "low quality", "bad anatomy", "watermark",
             "signature", "text"),
    "sd15": ("worst quality", "low quality", "bad anatomy", "watermark", "text"),
    "ltx": ("blurry", "distorted", "watermark"),
    "minimax": ("模糊", "变形", "水印"),
}
# 视频类家族：提示词应是自然语言（标签堆砌是常见误区）
_NL_FAMILIES = ("minimax", "ltx")
_MOTION_WORDS = ("camera", "zoom", "pan", "dolly", "tracking", "motion", "moving",
                 "turns", "walks", "runs", "sways", "drifts", "floats", "镜头",
                 "推近", "拉远", "移动", "转身", "走过", "飘", "缓慢", "动作")


def merge_negative(family: str, negative: str) -> tuple[str, bool]:
    """默认负面 ∪ 用户补充（去重）。返回 (合并结果, 是否发生合并)。

    大脑习惯整段自写负面词，实测 12/12 次都丢掉了默认里的
    watermark/signature/(worst quality:1.4) 等项——这里做保底合并。"""
    base = negative_for(family)
    user = (negative or "").strip()
    if not user:
        return base, True
    low = user.lower()
    keys = _NEG_KEYS.get(family, _NEG_KEYS["sdxl"])
    if all(k in low for k in keys):
        return user, False          # 关键项齐了：尊重大脑的写法
    merged = base + ", " + user
    return merged, True


def check_prompt(family: str, prompt: str, negative: str = "") -> dict:
    """提示词体检。返回 {prompt, warnings}；缺质量前缀时自动补齐。"""
    p = (prompt or "").strip()
    warnings: list[str] = []
    guide = guide_for(family)

    if family in ("sdxl", "sd15"):
        prefix = guide.get("quality_prefix") or ""
        first = p[:60].lower()
        if prefix and prefix.split(",")[0].strip().lower() not in first:
            p = (prefix + ", " + p) if p else prefix
            warnings.append("正向提示词缺质量词前缀，已自动补 "
                            f"{prefix!r}（家族规范：质量词在前）")
        if len(p.split()) > 90:
            warnings.append("正向提示词偏长（>90 词），SD 系 77 token 截断，"
                            "建议把关键内容前移")
    elif family in _NL_FAMILIES:
        segs = [s.strip() for s in p.split(",") if s.strip()]
        avg_words = (sum(len(s.split()) for s in segs) / len(segs)) if segs else 0
        has_motion = any(w in p.lower() for w in _MOTION_WORDS)
        if len(segs) >= 4 and avg_words <= 3.5 and not has_motion:
            warnings.append(
                f"{family} 编码器偏好自然语言（主体+动作+镜头+音效），"
                "当前是逗号短标签堆砌且没有动作/镜头词，建议改写为完整句子")
        elif not has_motion:
            warnings.append(f"{family} 提示词缺少动作/镜头词（如缓慢推近、"
                            "头发轻摆），视频容易变成静帧")

    neg_low = (negative or "").lower()
    p_low = p.lower()
    wants_cartoonish = any(w in p_low for w in
                           ("anime", "ghibli", "cartoon", "手绘", "动画"))
    if wants_cartoonish and any(w in neg_low for w in
                                ("cartoon", "anime", "illustration")):
        warnings.append("负面词与目标风格冲突：正向要动漫/吉卜力风格，"
                        "负面却禁 cartoon/anime/illustration，风格会被压制")
    if "realistic" in p_low and any(w in p_low for w in ("anime", "ghibli",
                                                         "cartoon", "动画")):
        warnings.append("正向提示词风格自相矛盾（同时要 anime 与 realistic），"
                        "建议二选一")
    return {"prompt": p, "warnings": warnings}


def prepare(family: str, prompt: str, negative: str) -> tuple[str, str, list[str]]:
    """渲染前统一处理：正负面体检 + 负面合并。返回 (prompt, negative, warnings)。"""
    chk = check_prompt(family, prompt, negative)
    merged_neg, changed = merge_negative(family, negative)
    warns = list(chk["warnings"])
    if changed:
        warns.append("负面提示词未包含家族默认项，已自动合并默认负面"
                     "（watermark/signature/质量词等）")
    return chk["prompt"], merged_neg, warns
