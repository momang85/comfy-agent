# -*- coding: utf-8 -*-
"""家族文档生成器：依据本机 object_info 快照 + 节点档案 + 工作流统计，
重建 brain/skills/families/*.md，保证文档里的每个类名都真实存在。

背景：初版家族文档由 LLM 生成、未经核验，实测大量类名本机不存在
（VAE_ENCODER / IPAdapterFaceIDKolors / ComposeVec…），自由合成时会把
大脑带偏。本脚本把家族文档变成"机器可推导、可断言"的产物：
- 核心节点 = 人工精选 + 快照过滤（不存在的自动剔除并报告）
- 接线证据 = node_usage.json（98 个真实工作流的统计）
- 参数/坑 = node_profiles.jsonl（1527 条实景档案）
- 硬断言：输出中每个类名都在快照里（防幻觉名回潮）

用法：python scripts/rebuild_family_docs.py
节点变动后重跑即可；CI 可加"生成后 git diff 为空"检查。
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from comfy_agent import config  # noqa: E402

SNAPSHOT_PATH = config.KNOWLEDGE_DIR / "object_info_snapshot.json"
PROFILES_PATH = config.KNOWLEDGE_DIR / "node_profiles.jsonl"
USAGE_PATH = config.KNOWLEDGE_DIR / "node_usage.json"
OUT_DIR = ROOT / "brain" / "skills" / "families"

# 每个家族：中文名 + 精选核心节点（可含本机没有的候选，脚本自动剔除）
FAMILIES: dict[str, dict] = {
    "model_loading.md": {
        "title": "模型加载",
        "nodes": ["CheckpointLoaderSimple", "CheckpointLoader", "UNETLoader",
                  "CLIPLoader", "DualCLIPLoader", "VAELoader", "CLIPVisionLoader",
                  "IPAdapterModelLoader", "UpscaleModelLoader", "ControlNetLoader",
                  "LoraLoader", "LoraLoaderModelOnly"],
        "tips": [
            "管线共享一个 CheckpointLoaderSimple：引擎 compose 会自动合并重复加载器",
            "VAE 优先用 checkpoint 槽 2（自带 VAE），避免 SD1.5/SDXL VAE 族错配",
            "LoraLoaderModelOnly 只改模型不连 CLIP；LoRA 权重叠加 ≤1.2（角色 0.7-0.9）",
        ],
    },
    "conditioning.md": {
        "title": "条件与提示词编码",
        "nodes": ["CLIPTextEncode", "CLIPTextEncodeSDXL",
                  "ConditioningCombine", "ConditioningConcat",
                  "ConditioningAverage", "ConditioningSetTimestepRange",
                  "ConditioningZeroOut", "ConditioningSetArea"],
        "tips": [
            "正/负分开两个 CLIPTextEncode 编码，分别接 KSampler 正负",
            "超 77 token：拆两个编码器 + ConditioningCombine（每个各有独立预算；"
            "主构图放第一个）",
            "别写 A1111 的 [a:b:0.5]（ComfyUI 用 ConditioningSetTimestepRange）",
            "正向里不写否定词（no hat/without）——CLIP 不做否定",
        ],
    },
    "sampling.md": {
        "title": "采样与调度",
        "nodes": ["KSampler", "KSamplerAdvanced", "KSamplerSelect",
                  "BasicScheduler", "RandomNoise", "SamplerCustomAdvanced",
                  "CFGGuider", "SamplerCustom", "AlignYourStepsScheduler"],
        "tips": [
            "SDXL：dpmpp_2m+karras 25-35 步 CFG 4-6；novaAnimeXL 官卡推荐 Euler a "
            "20-30 步 CFG 4-6",
            "SD1.5：euler/dpmpp_2m+karras 20-30 步 CFG 6-8",
            "蒸馏/turbo 模型：1-8 步、CFG≈1（多数不用负向）",
            "hires/多段管线用收敛型采样器（euler a 每步漂移，二段不可复现）",
        ],
    },
    "latent.md": {
        "title": "潜空间",
        "nodes": ["EmptyLatentImage", "LatentUpscale", "LatentUpscaleBy",
                  "LatentComposite", "SetLatentNoiseMask", "VAEEncode",
                  "VAEDecode", "VAEDecodeTiled", "LatentFromBatch"],
        "tips": [
            "hiresfix 标准链：KSampler → LatentUpscaleBy(1.5-2x, bicubic/bislerp) → "
            "KSampler(denoise 0.3-0.5, SD1.5 用 0.5-0.55)",
            "≥1536² 解码必须 VAEDecodeTiled(tile 512/overlap 64)：12GB 卡 2048² 整图"
            "解码需 14GB+",
        ],
    },
    "image_io.md": {
        "title": "图像读写与变换",
        "nodes": ["LoadImage", "LoadImageOutput", "LoadImageMask", "SaveImage",
                  "PreviewImage", "ImageScaleBy", "ImageScaleToTotalPixels",
                  "ImageFromBatch", "BatchImagesNode", "ImageBlend",
                  "ImageUpscaleWithModel"],
        "tips": [
            "LoadImage 的槽 0=IMAGE 槽 1=MASK（按 alpha）；遮罩白色=生效区",
            "纯像素放大用 ImageScaleBy（lanczos）；模型放大用 "
            "ImageUpscaleWithModel（本机未装 ESRGAN 文件时不可用）",
        ],
    },
    "controlnet.md": {
        "title": "ControlNet 控制",
        "nodes": ["Canny", "OpenposePreprocessor", "DWPreprocessor",
                  "DepthAnythingPreprocessor", "LineartStandardPreprocessor",
                  "ScribblePreprocessor", "HEDPreprocessor", "PiDiNetPreprocessor",
                  "ControlNetLoader", "ControlNetApply", "ControlNetApplyAdvanced"],
        "tips": [
            "强度惯例：canny 0.6-0.8（end_percent 0.6-1.0 提前释放细节）；"
            "openpose 0.7-1.0 全程；depth 0.6-0.9",
            "12GB 一次挂 1-2 个 CN；本机 SDXL 用 union promax 一模型多模式",
            "Canny 是核心节点离线可用；线稿/涂鸦/深度预处理器可能需联网下载模型",
        ],
    },
    "detection.md": {
        "title": "检测与细化",
        "nodes": ["FaceDetailer", "FaceDetailerPipe", "BboxDetectorCombined_v2",
                  "SegmDetectorCombined_v2", "CLIPSegDetectorProvider",
                  "ONNXDetectorProvider", "BboxDetectorSEGS", "SegmDetectorSEGS",
                  "SAMLoader", "SAMDetectorCombined", "SAMPreprocessor",
                  "SEGSPreview", "ToDetailerPipe", "ToDetailerPipeSDXL",
                  "ImageLuminanceDetector", "ImageIntensityDetector"],
        "tips": [
            "FaceDetailer 默认：guide_size 512 / steps 20 / cfg 8 / denoise 0.5 / "
            "feather 5 / noise_mask on；小脸调 bbox_threshold 0.2",
            "本机缺 face_yolov8m（只有手部模型）：FaceDetailer 的 bbox 检测不可用，"
            "可走 CLIPSegDetectorProvider/SAM(sam_vit_b) 检测链；装 face_yolov8m.pt "
            "后启用完整修脸",
        ],
    },
    "inpaint.md": {
        "title": "局部重绘与遮罩",
        "nodes": ["VAEEncodeForInpaint", "SetLatentNoiseMask", "InpaintModelConditioning",
                  "LoadImageMask", "MaskComposite", "GrowMask"],
        "tips": [
            "标准链：LoadImage+Mask → VAEEncodeForInpaint(grow_mask_by 6) → "
            "KSampler(denoise 0.5-0.7) → 解码；提示词只描述遮罩区内容",
            "SDXL 小区域重绘可上 CropAndStitch（本机未装则整图重绘）",
        ],
    },
    "audio.md": {
        "title": "音频",
        "nodes": ["LoadAudio", "SaveAudio", "SaveAudioMP3", "PreviewAudio",
                  "SplitAudioChannels", "TrimAudioDuration", "TextToSpeech",
                  "MergeTextLists"],
        "tips": [
            "MiniMax H3 音画同生：音效写进提示词句尾（\"with crisp glass cutting "
            "sounds\"），不需要音频节点",
            "独立音频处理走 VHS 节点；音频 A/B 拼接注意采样率一致",
        ],
    },
    "video.md": {
        "title": "视频",
        "nodes": ["LoadVideo", "GetVideoComponents", "ImageFromBatch",
                  "BatchImagesNode", "CreateVideo", "SaveVideo", "VHS_VideoCombine",
                  "MiniMaxH3ImageToVideo", "EmptyMiniMaxH3LatentAV",
                  "MiniMaxH3SigmaShift", "LTXVImgToVideo"],
        "tips": [
            "MiniMax H3：turbo LoRA strength 1.0 + 8 步；24fps、124 帧≈5 秒；"
            "官方 16:9 = 1344x768",
            "LTX 蒸馏版：8 步低 CFG；帧数 8k+1 对齐",
            "多段续接：extract_frame 取末帧 → 下一段 i2v → merge_videos 合并",
        ],
    },
    "logic.md": {
        "title": "逻辑与工具",
        "nodes": ["ImpactSwitch", "ImpactValueSender", "ImpactValueReceiver",
                  "ImpactLogicalOperators", "ImpactInt", "ImpactFloat",
                  "ImpactNeg", "ImpactMinMax", "ImpactIfNone", "ImpactDummyInput",
                  "ImpactStringSelector", "ImpactWildcardEncode",
                  "ImpactWildcardProcessor", "PrimitiveString",
                  "PrimitiveStringMultiline", "PrimitiveInt", "PrimitiveFloat",
                  "PrimitiveBoolean", "Seed (rgthree)"],
        "tips": [
            "生成任务尽量用模板参数，逻辑节点只在批量/切换需求时引入",
            "字符串模板用 PrimitiveStringMultiline 或 Impact 通配符节点",
        ],
    },
    "other.md": {
        "title": "其他常用",
        "nodes": ["ImageScaleToTotalPixels", "ImpactLogger", "ImpactLatentInfo",
                  "ImpactImageInfo", "String List to String"],
        "tips": [
            "ImpactWildcardEncode 支持通配符/LoRA 语法文本（见 logic.md）",
            "ImpactLogger 可调试中间值；ImpactLatentInfo/ImageInfo 打印张量信息",
        ],
    },
}

MAX_NODES_PER_FAMILY = 14


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_profiles(path: Path) -> dict:
    out = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("class"):
            out[d["class"]] = d
    return out


def _usage_links(usage: dict, cls: str, limit: int = 3) -> list[str]:
    """该类在本机真实工作流里输入接了什么（接地气接线证据）。"""
    node = usage.get(cls) or {}
    links = []
    for wf, ins in node.get("wfs", {}).items():
        for name, src in ins.items():
            if isinstance(src, str) and src:
                links.append(f"{name} ← {src}")
    seen, uniq = set(), []
    for l in links:
        if l not in seen:
            seen.add(l)
            uniq.append(l)
        if len(uniq) >= limit:
            break
    return uniq


def _param_hint(profile: dict, limit: int = 4) -> str:
    """从档案的 inputs 提炼参数提示（截断保持紧凑）。"""
    ins = profile.get("inputs") or []
    names = []
    if isinstance(ins, str):
        names = [s.split(":")[0].strip() for s in ins.split("；")[:limit]]
    elif isinstance(ins, list):
        for item in ins[:limit]:
            if isinstance(item, dict):
                names.append(item.get("name", ""))
            elif isinstance(item, str):
                names.append(item.split(":")[0].strip())
    names = [n for n in names if n and len(n) < 30][:limit]
    return ", ".join(names)


def _pitfall(profile: dict) -> str:
    pits = profile.get("pitfalls") or []
    if isinstance(pits, list) and pits:
        return str(pits[0])[:110]
    return ""


def main() -> None:
    snapshot = _load_json(SNAPSHOT_PATH)
    profiles = _load_profiles(PROFILES_PATH)
    usage = _load_json(USAGE_PATH)
    if not snapshot:
        print("[error] 快照缺失，先运行: python -m comfy_agent.cli refresh")
        sys.exit(1)

    report = []
    for fname, spec in FAMILIES.items():
        existing = [n for n in spec["nodes"] if n in snapshot]
        dropped = [n for n in spec["nodes"] if n not in snapshot]
        if dropped:
            report.append(f"{fname} 剔除不存在: {', '.join(dropped)}")

        # 类别补充：同 category 的其他节点（去重、上限）
        cat_nodes = []
        if len(existing) < MAX_NODES_PER_FAMILY:
            want_cat = fname.replace(".md", "")
            for cls, info in snapshot.items():
                if cls in existing:
                    continue
                cat = (info.get("category") or "").lower()
                if want_cat in cat or want_cat.rstrip("s") in cat:
                    cat_nodes.append(cls)
            cat_nodes.sort()
        picks = existing + cat_nodes[:MAX_NODES_PER_FAMILY - len(existing)]
        picks = picks[:MAX_NODES_PER_FAMILY]

        lines = [f"# {spec['title']}速查（本机实测节点）", ""]
        lines.append(f"> 生成于 {datetime.date.today()}，数据源 = 本机 "
                     f"object_info 快照（{len(snapshot)} 节点）+ 实景档案。"
                     f"所有类名经存在性断言。")
        lines.append("")
        lines.append("## 核心节点（本机存在）")
        for cls in picks:
            assert cls in snapshot, f"断言失败：{cls} 不在快照中"
            prof = profiles.get(cls, {})
            one = prof.get("one_line") or prof.get("purpose", "")[:80]
            hint = _param_hint(prof)
            line = f"- **{cls}** — {one[:100]}"
            if hint:
                line += f"（关键参数: {hint}）"
            lines.append(line)
            links = _usage_links(usage, cls)
            if links:
                lines.append(f"  - 本机接线: {'; '.join(links)}")
            pit = _pitfall(prof)
            if pit:
                lines.append(f"  - 坑: {pit}")
        lines.append("")
        lines.append("## 惯例与骨架")
        for t in spec["tips"]:
            lines.append(f"- {t}")
        lines.append("")
        lines.append("---")
        lines.append("本文档由 `scripts/rebuild_family_docs.py` 生成；"
                     "节点库变动后重跑：`python scripts/rebuild_family_docs.py`")

        (OUT_DIR / fname).write_text("\n".join(lines) + "\n",
                                     encoding="utf-8")
        report.append(f"{fname}: {len(picks)} 节点")

    print("\n".join(report))


if __name__ == "__main__":
    main()
