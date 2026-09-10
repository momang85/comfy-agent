# -*- coding: utf-8 -*-
"""图像模板：文生图（SDXL/SD1.5）、图生图、ControlNet 风格转绘。

文生图为手写标准结构；图生图/风格转绘结构对应用户已验证的工作流
（VAE 用 checkpoint 自带，避免本机缺失的 sdxlVAE 文件）。
"""
from __future__ import annotations

from .base import Param, Template, ksampler, clip_text_encode

SDXL_CKPT = "sdXL\\novaAnimeXL_ilV180.safetensors"
SD15_CKPT = "sd1.5\\anything-v5.safetensors"
# SD1.5 生态的通用负面嵌入（本机 embeddings 为空，用文本负面词）
NEG_SD = ("(worst quality, low quality:1.4), bad anatomy, bad hands, "
          "extra digits, fewer digits, missing fingers, watermark, "
          "signature, text, jpeg artifacts")
NEG_SDXL = ("(worst quality, low quality:1.4), bad anatomy, bad hands, "
            "extra limbs, missing limbs, watermark, signature, text, "
            "logo, cropped, out of frame")


class T2I(Template):
    """文生图模板（SDXL 默认 / SD1.5 轻量）。"""
    id = "t2i"
    name = "文生图"
    category = "image"
    desc = "文本生成图像。动漫SDXL(novaAnimeXL,默认)或轻量SD1.5(anything-v5)。"
    est_vram_gb = 7.0
    est_minutes = "1-3"

    def __init__(self, ckpt: str = SDXL_CKPT):
        self.ckpt = ckpt
        self.family = "sdxl" if "xl" in ckpt.lower() else "sd15"
        self.models_used = [ckpt]

    def params(self):
        return [
            Param("prompt", "str", "", "正向提示词", required=True,
                  desc="英文标签风格（SDXL/SD1.5 用逗号分隔的 booru 标签）"),
            Param("negative", "str", NEG_SDXL if self.family == "sdxl" else NEG_SD,
                  "负面提示词"),
            Param("width", "int", 1024 if self.family == "sdxl" else 512,
                  "宽", minv=256, maxv=2048,
                  desc="SDXL 最佳分辨率簇 1024x1024 / 1152x896 / 896x1152，"
                       "低于 768 质量明显下降"),
            Param("height", "int", 1024 if self.family == "sdxl" else 512,
                  "高", minv=256, maxv=2048,
                  desc="同宽：SDXL 建议 1024 簇"),
            Param("batch", "int", 1, "张数", minv=1, maxv=8),
            Param("steps", "int", 24, "步数", minv=4, maxv=60),
            Param("cfg", "float", 7.0, "CFG", minv=1.0, maxv=15.0),
            Param("seed", "int", 0, "种子(0=随机)", minv=0),
            Param("sampler", "choice", "dpmpp_2m", "采样器",
                  choices=["euler", "euler_ancestral", "dpmpp_2m", "dpmpp_2m_sde",
                           "dpmpp_3m_sde", "ddim", "uni_pc"]),
            Param("scheduler", "choice", "karras", "调度器",
                  choices=["karras", "simple", "beta", "normal",
                           "exponential", "sgm_uniform", "ddim_uniform"],
                  desc="karras 配 dpmpp_2m 是 SDXL/SD1.5 社区常用组合"),
            Param("ckpt", "str", self.ckpt, "模型"),
        ]

    def render(self, p: dict):
        q = self._fill_defaults(p, self.params())
        ckpt = q.get("ckpt") or self.ckpt
        seed = q["seed"] or _rand_seed()
        return {
            "1": {"class_type": "CheckpointLoaderSimple",
                  "inputs": {"ckpt_name": ckpt},
                  "_meta": {"title": "加载模型"}},
            "2": clip_text_encode(["1", 1], q["prompt"]),
            "3": clip_text_encode(["1", 1], q["negative"] or ""),
            "4": {"class_type": "EmptyLatentImage", "inputs": {
                "width": q["width"], "height": q["height"],
                "batch_size": q["batch"]}},
            "5": ksampler(["1", 0], ["2", 0], ["3", 0], ["4", 0],
                          seed=seed, steps=q["steps"], cfg=q["cfg"],
                          sampler=q["sampler"], scheduler=q["scheduler"],
                          denoise=1.0),
            "6": {"class_type": "VAEDecode", "inputs": {
                "samples": ["5", 0], "vae": ["1", 2]}},
            "7": {"class_type": "SaveImage", "inputs": {
                "images": ["6", 0], "filename_prefix": "agent_t2i"}},
        }


class I2I(T2I):
    """图生图模板（结构对应用户验证过的 图生图.json，VAE 用 checkpoint 自带）。"""
    id = "i2i"
    name = "图生图"
    category = "image"
    desc = "上传图片+提示词重绘。保持构图改风格/改内容。"
    est_vram_gb = 7.0
    est_minutes = "1-3"

    def params(self):
        base = super().params()
        return [
            Param("image", "image", "", "输入图片", required=True,
                  desc="用户上传的图片（agent 自动上传到 /input）"),
            Param("denoise", "float", 0.65, "重绘幅度",
                  minv=0.1, maxv=1.0,
                  desc="低=贴近原图，高=更听提示词"),
            Param("resize_mode", "choice", "justify", "缩放模式",
                  choices=["justify", "center", "disabled"]),
        ] + [p for p in base if p.name not in ("width", "height")]

    def render(self, p: dict):
        q = self._fill_defaults(p, self.params())
        ckpt = q.get("ckpt") or self.ckpt
        seed = q["seed"] or _rand_seed()
        pixels = 1024*1024 if self.family == "sdxl" else 512*512
        return {
            "1": {"class_type": "LoadImage",
                  "inputs": {"image": q["image"] or "example.png"}},
            "2": {"class_type": "ImageScaleToTotalPixels", "inputs": {
                "upscale_method": "lanczos",
                "megapixels": 1.0 if self.family == "sdxl" else 0.25,
                "resolution_steps": 1,
                "image": ["1", 0]}},
            "3": {"class_type": "VAEEncode", "inputs": {
                "pixels": ["2", 0], "vae": ["5", 2]}},
            "4": clip_text_encode(["5", 1], q["prompt"]),
            "6": clip_text_encode(["5", 1], q["negative"] or ""),
            "5": {"class_type": "CheckpointLoaderSimple",
                  "inputs": {"ckpt_name": ckpt}},
            "7": ksampler(["5", 0], ["4", 0], ["6", 0], ["3", 0],
                          seed=seed, steps=q["steps"], cfg=q["cfg"],
                          sampler=q["sampler"], scheduler=q["scheduler"],
                          denoise=q["denoise"]),
            "8": {"class_type": "VAEDecode", "inputs": {
                "samples": ["7", 0], "vae": ["5", 2]}},
            "9": {"class_type": "SaveImage", "inputs": {
                "images": ["8", 0], "filename_prefix": "agent_i2i"}},
        }


class StyleTransfer(Template):
    """ControlNet 风格转绘（结构对应用户验证过的 风格转绘.json）。"""
    id = "style_transfer"
    name = "风格转绘"
    category = "image"
    family = "sdxl"
    desc = "保持姿势/线条换画风（SDXL+ControlNet）。canny=锁构图(离线)；openpose=锁人物姿势(需联网下载模型)"
    models_used = [SDXL_CKPT,
                   "controlnetxlCNXL_2vxpswa7OpenposeV21.safetensors",
                   "controlnet++_union_sdxl_promax.safetensors"]
    est_vram_gb = 9.0
    est_minutes = "2-4"

    # 控制类型 -> (预处理器节点, 控制模型文件)
    CONTROLNETS = {
        "openpose": ("OpenposePreprocessor",
                     "controlnetxlCNXL_2vxpswa7OpenposeV21.safetensors"),
        "canny": ("Canny", "controlnet++_union_sdxl_promax.safetensors"),
        "anytest": ("Canny", "controlnetxlCNXL_2vxpswa7AnytestV4.safetensors"),
    }

    def params(self):
        return [
            Param("image", "image", "", "输入图片", required=True),
            Param("prompt", "str", "", "正向提示词（目标风格）", required=True),
            Param("negative", "str", NEG_SDXL, "负面提示词"),
            Param("control_type", "choice", "canny", "控制类型",
                  choices=["canny", "openpose", "anytest"],
                  desc="canny=锁构图（离线可用，推荐）；openpose=锁人物姿势（需联网下载预处理模型）"),
            Param("strength", "float", 0.9, "ControlNet强度", minv=0.1, maxv=1.0),
            Param("steps", "int", 30, "步数", minv=8, maxv=60),
            Param("cfg", "float", 6.0, "CFG", minv=1.0, maxv=12.0),
            Param("denoise", "float", 1.0, "重绘幅度", minv=0.3, maxv=1.0,
                  desc="换风格建议 0.6-0.85；0.4-0.55 只做微调（风格变化会很不明显）"),
            Param("seed", "int", 0, "种子(0=随机)"),
            Param("sampler", "choice", "dpmpp_2m", "采样器",
                  choices=["euler", "euler_ancestral", "dpmpp_2m", "dpmpp_2m_sde",
                           "dpmpp_3m_sde", "ddim", "uni_pc"]),
            Param("scheduler", "choice", "karras", "调度器",
                  choices=["karras", "simple", "beta", "normal",
                           "exponential", "sgm_uniform", "ddim_uniform"]),
            Param("ckpt", "str", SDXL_CKPT, "模型"),
        ]

    def render(self, p: dict):
        q = self._fill_defaults(p, self.params())
        seed = q["seed"] or _rand_seed()
        preproc, cn_file = self.CONTROLNETS.get(q["control_type"],
                                                self.CONTROLNETS["canny"])
        # 预处理器输入（Canny 与 OpenPose 的输入参数不同）
        if preproc == "Canny":
            pre_inputs = {"image": ["1", 0], "low_threshold": 0.3,
                          "high_threshold": 0.7}
        else:
            pre_inputs = {"image": ["1", 0], "detect_hand": "enable",
                          "detect_body": "enable", "detect_face": "enable"}
        return {
            "1": {"class_type": "LoadImage", "inputs": {"image": q["image"] or "example.png"}},
            "5": {"class_type": "CheckpointLoaderSimple",
                  "inputs": {"ckpt_name": q["ckpt"]}},
            "10": {"class_type": preproc, "inputs": pre_inputs},
            "11": {"class_type": "ControlNetLoader", "inputs": {"control_net_name": cn_file}},
            "12": {"class_type": "ControlNetApplyAdvanced", "inputs": {
                "positive": ["13", 0], "negative": ["14", 0],
                "control_net": ["11", 0], "image": ["10", 0],
                "strength": q["strength"], "start_percent": 0.0,
                "end_percent": 1.0}},
            "13": clip_text_encode(["5", 1], q["prompt"]),
            "14": clip_text_encode(["5", 1], q["negative"] or ""),
            "15": {"class_type": "VAEEncode", "inputs": {
                "pixels": ["1", 0], "vae": ["5", 2]}},
            "16": ksampler(["5", 0], ["12", 0], ["12", 1], ["15", 0],
                           seed=seed, steps=q["steps"], cfg=q["cfg"],
                           sampler=q["sampler"], scheduler=q["scheduler"],
                           denoise=q["denoise"]),
            "17": {"class_type": "VAEDecode", "inputs": {
                "samples": ["16", 0], "vae": ["5", 2]}},
            "18": {"class_type": "SaveImage", "inputs": {
                "images": ["17", 0], "filename_prefix": "agent_style"}},
        }


class UpscalePass(Template):
    """高清放大（二次采样 hiresfix 变体）：输入图 → 2x 缩放 → 低重绘重采样。

    零外部模型依赖（无 ESRGAN 文件也能跑）；常与 t2i/i2i 拼接成管线。"""
    id = "upscale_pass"
    name = "高清放大"
    category = "upscale"
    family = "sdxl"
    desc = "输入图片放大2倍并重绘细节（lanczos缩放+低重绘二次采样，无需放大模型文件）"
    models_used = [SDXL_CKPT]
    est_vram_gb = 7.0
    est_minutes = "1-3"

    def params(self):
        return [
            Param("image", "image", "", "输入图片", required=True,
                  desc="要被放大的图（管线拼接时由上游模板产出）"),
            Param("prompt", "str", "", "画面内容简述（用于重绘保真）",
                  required=True),
            Param("negative", "str", NEG_SDXL, "负面提示词"),
            Param("scale", "float", 2.0, "放大倍数", minv=1.2, maxv=4.0),
            Param("denoise", "float", 0.45, "重绘幅度", minv=0.15, maxv=0.6,
                  desc="低=保真，高=更多细节重绘"),
            Param("steps", "int", 20, "步数", minv=8, maxv=40),
            Param("cfg", "float", 7.0, "CFG", minv=1.0, maxv=12.0),
            Param("seed", "int", 0, "种子(0=随机)"),
            Param("sampler", "choice", "dpmpp_2m", "采样器",
                  choices=["euler", "euler_ancestral", "dpmpp_2m", "dpmpp_2m_sde",
                           "dpmpp_3m_sde", "ddim", "uni_pc"]),
            Param("scheduler", "choice", "karras", "调度器",
                  choices=["karras", "simple", "beta", "normal",
                           "exponential", "sgm_uniform", "ddim_uniform"]),
            Param("ckpt", "str", SDXL_CKPT, "模型"),
        ]

    def render(self, p: dict):
        q = self._fill_defaults(p, self.params())
        if not q.get("image"):
            raise ValueError(
                "upscale_pass 必须提供 image 参数（要放大的图）。"
                "若接在上一步生成之后，用上一步 run_template 返回的 "
                "server_images[0] 作为 image；或用 compose 管线拼接。")
        seed = q["seed"] or _rand_seed()
        return {
            "1": {"class_type": "LoadImage",
                  "inputs": {"image": q["image"]}},
            "2": {"class_type": "ImageScaleBy", "inputs": {
                "upscale_method": "lanczos", "scale_by": q["scale"],
                "image": ["1", 0]}},
            "3": {"class_type": "CheckpointLoaderSimple",
                  "inputs": {"ckpt_name": q["ckpt"]}},
            "4": clip_text_encode(["3", 1], q["prompt"]),
            "5": clip_text_encode(["3", 1], q["negative"] or ""),
            "6": {"class_type": "VAEEncode", "inputs": {
                "pixels": ["2", 0], "vae": ["3", 2]}},
            "7": ksampler(["3", 0], ["4", 0], ["5", 0], ["6", 0],
                          seed=seed, steps=q["steps"], cfg=q["cfg"],
                          sampler=q["sampler"], scheduler=q["scheduler"],
                          denoise=q["denoise"]),
            "8": {"class_type": "VAEDecode", "inputs": {
                "samples": ["7", 0], "vae": ["3", 2]}},
            "9": {"class_type": "SaveImage", "inputs": {
                "images": ["8", 0], "filename_prefix": "agent_upscale"}},
        }


def _rand_seed():
    import secrets
    return secrets.randbelow(2**31 - 1) + 1


TEMPLATES_IMAGE = [T2I(SDXL_CKPT), T2I(SD15_CKPT), I2I(SDXL_CKPT),
                   StyleTransfer(), UpscalePass()]
