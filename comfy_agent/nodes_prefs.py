# -*- coding: utf-8 -*-
"""能力 → 优先节点索引：用户意图映射到"本机可用的最优节点/链路"。

大脑面对 1500+ 节点时，需要的是"需要某功能先用什么"的确定性知识，
而不是每次现场 search_nodes 试错。设计要点：

- 每项能力给出按优先级排序的候选链路（节点类 + 所需模型文件 + 接线骨架）
- resolve() 运行时逐项核验（节点存在 + 模型存在），全部不满足时返回
  fallback 降级路线（给大脑的指引，而不是抛错）
- capability_summary() 生成紧凑摘要注入大脑系统提示（与模板目录同级）
- 接线骨架用于 health.py 的真实连线审计（能不能连上、类型对不对）

本机的实况结论（comfy_agent/health.py 会再次核验）：
- FaceDetailer 缺 face_yolov8m（只有手部模型）→ 修脸降级 i2i 低 denoise
- upscale_models 目录空 → 放大走 LatentUpscaleBy（零模型）
- ipadapter/clip_vision 模型空 → 风格参考降级 ControlNet（全套已装）
- DWPreprocessor 需 DWPosePoseEstimator（未装）→ 锁姿势用 OpenposePreprocessor
"""
from __future__ import annotations

CAPABILITIES: list[dict] = [
    {
        "id": "face_fix", "name": "修脸/脸部细化",
        "candidates": [
            {"class": "FaceDetailer", "models": ["face_yolov8m"],
             "skeleton": ("ks", "detail"),
             "note": "Impact Pack 人脸细化；detection 需 face_yolov8m.pt"},
        ],
        "fallback": "本机缺 face_yolov8m：改用 run_template(inpaint) 或 i2i 低 denoise"
                    "(0.3-0.45) 局部重绘（repair.md 配方）；装 face_yolov8m.pt/"
                    "face_yolov8n_v2.pt 后启用 FaceDetailer（guide_size 512, "
                    "denoise 0.4-0.5, feather 10）",
    },
    {
        "id": "pose_lock", "name": "锁定人物姿势",
        "candidates": [
            {"class": "OpenposePreprocessor",
             "models": ["controlnetxlCNXL_2vxpswa7OpenposeV21"],
             "skeleton": ("pose_cn",),
             "note": "自带检测无需外接；预处理模型可能需联网下载，失败换 anytest"},
            {"class": "DWPreprocessor",
             "models": ["controlnetxlCNXL_2vxpswa7OpenposeV21"],
             "skeleton": ("pose_cn",),
             "note": "需 DWPosePoseEstimator 输入（本机未装时不可用）"},
        ],
        "fallback": "style_transfer 模板 control_type=openpose",
    },
    {
        "id": "composition_lock", "name": "锁定构图/边缘",
        "candidates": [
            {"class": "Canny", "models": ["controlnet++_union_sdxl_promax"],
             "skeleton": ("canny_cn",),
             "note": "离线安全，style_transfer 模板默认"},
            {"class": "Canny", "models": ["controlnetxlCNXL_bdsqlszCanny"],
             "skeleton": ("canny_cn",), "note": "SDXL canny 专用控制网"},
            {"class": "Canny", "models": ["control_v11p_sd15_canny_fp16"],
             "skeleton": ("canny_cn",), "note": "SD1.5 canny"},
        ],
        "fallback": "style_transfer 模板 control_type=canny",
    },
    {
        "id": "lineart", "name": "线稿控制",
        "candidates": [
            {"class": "LineartStandardPreprocessor",
             "models": ["control_v11p_sd15s2_lineart_anime_fp16"],
             "skeleton": ("lineart_cn",), "note": "SD1.5 动漫线稿（配 anything-v5）"},
            {"class": "LineartStandardPreprocessor",
             "models": ["control_v11p_sd15_lineart_fp16"],
             "skeleton": ("lineart_cn",)},
        ],
        "fallback": "用 canny 近似锁构图",
    },
    {
        "id": "depth_control", "name": "深度/空间控制",
        "candidates": [
            {"class": "DepthAnythingPreprocessor",
             "models": ["controlnetxlCNXL_bdsqlszDepth"],
             "skeleton": ("depth_cn",), "note": "SDXL depth"},
            {"class": "DepthAnythingPreprocessor",
             "models": ["control_v11f1p_sd15_depth_fp16"],
             "skeleton": ("depth_cn",), "note": "SD1.5 depth"},
        ],
        "fallback": "用 canny 近似；预处理器联网失败时换 SD1.5 控制网",
    },
    {
        "id": "segment", "name": "语义分割控制",
        "candidates": [
            {"class": "Canny", "models": ["controlnetxlCNXL_abovzvSegment"],
             "skeleton": ("segment_cn",), "note": "SDXL segment 控制网"},
            {"class": "Canny", "models": ["control_v11p_sd15_seg_fp16"],
             "skeleton": ("segment_cn",)},
        ],
        "fallback": "SAM 检测（SAMLoader + sam_vit_b）后接局部重绘链",
    },
    {
        "id": "upscale", "name": "高清放大",
        "candidates": [
            {"class": "LatentUpscaleBy", "models": [],
             "skeleton": ("hiresfix",),
             "note": "潜空间放大（hiresfix 核心，零模型依赖），接第二次低 denoise 采样"},
            {"class": "ImageUpscaleWithModel", "models": ["4x-UltraSharp"],
             "skeleton": None,
             "note": "ESRGAN 像素放大（本机未装模型时不可用）"},
            {"class": "UltimateSDUpscale", "models": ["4x-UltraSharp"],
             "skeleton": None,
             "note": "分块高清重绘（模型缺失时不可用）"},
        ],
        "fallback": "ImageScaleBy lanczos 2x + 低 denoise 二次采样（upscale_pass 模板，"
                    "零模型依赖）；装 4x-AnimeSharp（动漫）/4x-UltraSharp（写实）"
                    ".pth 后可启用 ESRGAN 链路",
    },
    {
        "id": "style_ref", "name": "风格参考图",
        "candidates": [
            {"class": "IPAdapterUnifiedLoader", "models": ["ip-adapter"],
             "skeleton": None,
             "note": "IPAdapter 风格/构图迁移（本机未装 ipadapter 模型时不可用）"},
        ],
        "fallback": "style_transfer 模板（ControlNet canny 锁构图换风格）或 i2i；"
                    "装 ip-adapter_sdxl_vit-h.safetensors 后启用 IPAdapter",
    },
    {
        "id": "local_inpaint", "name": "局部重绘/去物",
        "candidates": [
            {"class": "VAEEncodeForInpaint", "models": [],
             "skeleton": ("inpaint",),
             "note": "直接 run_template(inpaint)（原图+遮罩，零额外模型）"},
        ],
        "fallback": "SD1.5 加 control_v11p_sd15_inpaint 控制网强化边缘融合",
    },
    {
        "id": "remove_bg", "name": "抠图/去背景",
        "candidates": [],
        "fallback": "本机未装 RMBG 节点（ComfyUI-Manager 搜索 ComfyUI-RMBG 安装后可用）",
    },
    {
        "id": "relight", "name": "补光/重打光",
        "candidates": [],
        "fallback": "本机未装 IC-Light（ComfyUI-Manager 安装 ComfyUI-IC-Light）",
    },
    {
        "id": "interp", "name": "视频插帧/补帧",
        "candidates": [],
        "fallback": "本机未装 Frame Interpolation 节点（ComfyUI-Manager 安装后可用）",
    },
    {
        "id": "sd15_quality", "name": "SD1.5 负向嵌入",
        "candidates": [],
        "fallback": "本机 embeddings 目录为空：用文本负面词（模板默认已带）；"
                    "装 easynegative/bad-hands 嵌入后可在 negative 前引用",
    },
]


def resolve(knowledge, cap: dict) -> dict:
    """挑出第一个本机可用的候选链路。返回 {ok, class, models, note, fallback?}"""
    for cand in cap.get("candidates", []):
        if not knowledge.has_node(cand["class"]):
            continue
        missing = [m for m in cand.get("models", []) if not knowledge.find_model(m)]
        if not missing:
            return {"ok": True, "class": cand["class"],
                    "models": cand.get("models", []),
                    "note": cand.get("note", "")}
    return {"ok": False, "fallback": cap.get("fallback", "")}


def capability_table(knowledge) -> list[dict]:
    """完整审计表（节点存在性 + 模型存在性 + 最终选择），CLI/健康检查用。"""
    rows = []
    for cap in CAPABILITIES:
        r = resolve(knowledge, cap)
        rows.append({"id": cap["id"], "name": cap["name"], "ok": r["ok"],
                     "use": r.get("class") or "(降级)",
                     "models": r.get("models", []),
                     "note": r.get("note", "") or r.get("fallback", "")[:140]})
    return rows


def capability_summary(knowledge) -> str:
    """给 LLM 的紧凑摘要（每行一个能力：优先用什么/缺什么怎么降级）。"""
    lines = ["## 能力→节点偏好（本机已核验，需要某功能时先看这里）"]
    for cap in CAPABILITIES:
        r = resolve(knowledge, cap)
        if r["ok"]:
            models = ("依赖: " + ", ".join(r["models"])) if r["models"] \
                else "零模型依赖"
            note = ("；" + r["note"]) if r.get("note") else ""
            lines.append(f"- {cap['name']}: {r['class']}（{models}{note}）")
        else:
            lines.append(f"- {cap['name']}: 不可用 → {r.get('fallback', '')[:110]}")
    return "\n".join(lines)
