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
