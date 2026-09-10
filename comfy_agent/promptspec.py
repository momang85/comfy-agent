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
    "sd15": ("(worst quality, low quality, normal quality:1.4), bad anatomy, "
             "bad hands, extra digits, fewer digits, missing fingers, "
             "watermark, signature, text"),
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


# 图像提示词分块结构 + 每块词库（引擎侧体检用，大脑侧详见 skills/prompts.md）。
# 主体块无固定词库（由任务决定）；其余块用于检查"这一维度有没有写到"。
BLOCK_VOCAB = {
    "quality": ["masterpiece", "best quality", "absurdres", "highly detailed",
                "8k wallpaper", "ultra-detailed", "sharp focus", "best shadow"],
    "appearance": ["detailed eyes", "detailed face", "intricate hair",
                   "fluffy fur", "detailed skin", "detailed clothes",
                   "detailed armor", "jewelry", "ribbon", "hair ornament",
                   "headwear", "glasses", "earrings", "frills", "lace"],
    "action": ["sitting", "standing", "walking", "running", "lying down",
               "reaching out", "looking at viewer", "looking away",
               "holding", "dancing", "jumping", "flying", "floating",
               "hand on chin", "arms crossed", "turning around", "smiling",
               "waving", "reading a book", "sipping tea"],
    "environment": ["indoors", "outdoors", "forest", "beach", "city street",
                    "rooftop", "library", "cafe", "classroom", "bedroom",
                    "mountain", "lake", "flower field", "starry sky",
                    "underwater", "space station", "rainy street",
                    "snowy landscape", "cherry blossoms", "bamboo grove",
                    "shrine", "alley", "garden", "bridge", "port"],
    "lighting": ["golden hour", "soft rim light", "volumetric lighting",
                 "backlighting", "sunlight through window", "moonlight",
                 "neon glow", "lens flare", "soft shadows", "dramatic lighting",
                 "cinematic lighting", "diffuse daylight", "candlelight",
                 "sunset glow", "blue hour", "studio lighting", "dappled light"],
    "atmosphere": ["serene", "cozy", "mysterious", "epic", "romantic",
                   "melancholic", "dreamy", "whimsical", "festive",
                   "nostalgic", "tense", "magical", "ethereal", "lively",
                   "intimate", "heroic"],
    "composition": ["close-up", "portrait", "full body", "cowboy shot",
                    "from above", "from below", "wide shot", "dutch angle",
                    "depth of field", "bokeh", "rule of thirds",
                    "symmetrical", "centered", "dynamic angle", "panoramic"],
    "style": ["anime style", "watercolor", "oil painting", "pixel art",
              "flat illustration", "semi-realistic", "cel shading",
              "Studio Ghibli style", "retro anime 90s", "ink wash painting",
              "pastel art", "photorealistic"],
}

# 视频族词库：动作/镜头/光影/音效（提示词体检与大脑词库共用）
VIDEO_VOCAB = {
    "motion": ["缓慢转身", "轻轻点头", "发丝飘动", "裙摆轻扬", "缓步走来",
               "奔跑", "回头", "挥手", "闭眼微笑", "眨眼", "深呼吸",
               "指尖轻触", "漂浮", "旋转", "迈步", "转头", "抬头",
               "waving", "walking", "turning", "nodding", "drifting"],
    "camera": ["镜头缓慢推近", "缓缓拉远", "环绕拍摄", "平移跟随", "俯拍",
               "仰拍", "固定机位", "手持晃动", "第一人称", "快速甩镜",
               "dolly in", "pan left", "pan right", "zoom in", "orbit",
               "static shot", "tracking shot"],
    "lighting": ["晨光", "黄昏逆光", "月光", "霓虹灯光", "烛光", "雾气",
                 "阳光透过窗户", "波光粼粼", "golden hour", "rim light",
                 "soft shadows"],
    "audio": ["风声", "雨声", "海浪声", "鸟鸣", "脚步声", "轻音乐",
              "琴声", "环境白噪音", "心跳声", "人群低语", "钟声",
              "引擎声", "树叶沙沙", "水滴声"],
}


def block_coverage(family: str, prompt: str) -> dict:
    """检查提示词覆盖了哪些维度块。返回 {present, missing, suggest}。"""
    p = (prompt or "").lower()
    blocks = BLOCK_VOCAB if family in ("sdxl", "sd15") else VIDEO_VOCAB
    present, missing = [], []
    for name, words in blocks.items():
        if name in ("quality",) and family in ("sdxl", "sd15"):
            continue                     # 质量词已由 check_prompt 单独处理
        if any(w.lower() in p for w in words):
            present.append(name)
        else:
            missing.append(name)
    suggest = {name: BLOCK_VOCAB.get(name, VIDEO_VOCAB.get(name, []))[:4]
               for name in missing}
    return {"present": present, "missing": missing, "suggest": suggest}


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
    # 维度覆盖体检：缺"光影/构图/动作/氛围"等维度块时给警告+词库建议。
    # 只对英文标签做（中文自然语言提示词不按英文词库分词）。
    ascii_ratio = sum(1 for c in p if ord(c) < 128) / max(len(p), 1)
    if ascii_ratio > 0.6:
        cov = block_coverage(family, p)
        if cov["missing"]:
            tips = "；".join(
                f"{m} 如 {', '.join(cov['suggest'][m][:3])}"
                for m in cov["missing"][:3])
            warnings.append("提示词维度不完整（缺 " + "、".join(cov["missing"])
                            + "），建议补充：" + tips)
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
