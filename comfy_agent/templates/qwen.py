# -*- coding: utf-8 -*-
"""Qwen-Image 2.1 指令改图模板。

为什么单开一个模块：这一族不用 CheckpointLoaderSimple（没有 checkpoint），
而是 UNet + 文本编码器 + VAE 三段式加载，且提示词走"指令句"而不是标签，
所以既不能挂到 T2I/I2I 下面，也不能继承它们的 render。

本机实测结论（RTX 5070 Ti Laptop 12GB / ComfyUI v0.38.0 / torch 2.13 cu130）：
1. TextEncodeQwenImage21 输出 (positive, negative, latent) 三路，latent 的尺寸
   取自"第一张参考图"，因此只接 LoadImage 就会得到原图尺寸（768 进 → 768 出）；
   接上 vae 后参考图改走 VAE 潜变量，输出尺寸由 resolution 参数按 32 对齐决定。
2. 给不给 vae 都能出图（两次生成的图肉眼几乎一致），但给 vae 时
   `keep_vision=False`，参考图不再进视觉塔——v1 默认给 vae，路径更短、显存更省。
3. QwenImageDiffsynthControlnet（fun controlnet union 结构控制）实测出的是花屏：
   把原图当 IMAGE 直插补丁不是正确接法，v1 不接，等后续单独验证。
4. cfg=1.0、steps=25、res_multistep+simple、ModelSamplingAuraFlow(shift=3,
   sampling="flow") 是本机官方工作流验证过的组合，原样沿用。
"""
from __future__ import annotations

from .base import Param, Template

QWEN_UNET = "qwen_image_2.1_int8_convrot.safetensors"
QWEN_TE = "qwen3vl_8b_int8_convrot.safetensors"
QWEN_VAE = "qwen_image_2.1_vae_bf16.safetensors"
# 结构控制补丁（本机已有，但 v1 的正确接法未验证，先不放进 models_used）
QWEN_PATCH = "qwen_image_2.1_fun_controlnet_union_int8_convrot.safetensors"

NEG_QWEN = "模糊, 变形, 低质量, 水印, 多余文字"


class QwenEdit(Template):
    """Qwen-Image 2.1 指令改图（参考图 + 一句自然语言指令）。"""

    id = "qwen_edit"
    name = "Qwen 指令改图"
    category = "image"
    family = "qwen"
    desc = ("上传一张图，用一句自然语言指令改它（换颜色/换材质/加物件/改风格），"
            "其余部分尽量保持不动。需要 Qwen-Image 2.1 三件套模型。")
    models_used = [QWEN_UNET, QWEN_TE, QWEN_VAE]
    est_vram_gb = 12.0
    est_minutes = "1-2"

    def params(self):
        return [
            Param("prompt", "str", "", "改图指令", required=True,
                  desc="一句话说清要改什么，不要写标签；如「把圆变成亮蓝色，"
                       "其余保持不动」"),
            Param("image", "image", "", "参考图", required=True,
                  desc="要改的图（agent 自动上传到 /input）"),
            Param("negative", "str", NEG_QWEN, "负面提示词"),
            Param("resolution", "int", 1024, "输出边长", minv=512, maxv=1536,
                  unit="px（32 的倍数）",
                  desc="输出为正方形，边长按 32 对齐；建议 1024"),
            Param("steps", "int", 25, "采样步数", minv=4, maxv=60),
            Param("cfg", "float", 1.0, "CFG", minv=0.0, maxv=8.0,
                  desc="Qwen-Image 蒸馏过，1.0 即可；调大会出硬边"),
            Param("sampler", "choice", "res_multistep", "采样器",
                  choices=["res_multistep", "euler", "euler_ancestral",
                           "dpmpp_2m", "uni_pc"],
                  desc="res_multistep 是官方工作流用的"),
            Param("scheduler", "choice", "simple", "调度器",
                  choices=["simple", "sgm_uniform", "beta", "karras"]),
            Param("shift", "float", 3.0, "采样位移", minv=0.5, maxv=8.0,
                  desc="ModelSamplingAuraFlow 的 shift，官方图用 3"),
            Param("seed", "int", 0, "随机种子", minv=0, maxv=2 ** 32 - 1),
        ]

    def render(self, p: dict):
        q = self._fill_defaults(p, self.params())
        res = int(q["resolution"] or 1024)
        res = max(512, min(1536, (res // 32) * 32))
        seed = int(q["seed"] or 0)
        return {
            "1": {"class_type": "UNETLoader", "inputs": {
                "unet_name": QWEN_UNET, "weight_dtype": "default"}},
            "2": {"class_type": "CLIPLoader", "inputs": {
                "clip_name": QWEN_TE, "type": "qwen_image",
                "device": "default"}},
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": QWEN_VAE}},
            "4": {"class_type": "LoadImage", "inputs": {
                "image": q["image"] or "example.png"}},
            "5": {"class_type": "ModelSamplingAuraFlow", "inputs": {
                "model": ["1", 0], "shift": float(q["shift"] or 3.0),
                "sampling": "flow"}},
            # 三路输出：positive / negative / latent（latent 由参考图决定）
            "6": {"class_type": "TextEncodeQwenImage21", "inputs": {
                "clip": ["2", 0],
                "prompt": q["prompt"] or "",
                "negative_prompt": q["negative"] or "",
                "vae": ["3", 0],
                "resolution": res,
                "images": {"image_1": ["4", 0]}}},
            "7": {"class_type": "KSampler", "inputs": {
                "model": ["5", 0],
                "seed": seed,
                "steps": int(q["steps"] or 25),
                "cfg": float(q["cfg"] if q["cfg"] is not None else 1.0),
                "sampler_name": q["sampler"] or "res_multistep",
                "scheduler": q["scheduler"] or "simple",
                "positive": ["6", 0],
                "negative": ["6", 1],
                "latent_image": ["6", 2],
                "denoise": 1.0}},
            "8": {"class_type": "VAEDecode", "inputs": {
                "samples": ["7", 0], "vae": ["3", 0]}},
            "9": {"class_type": "SaveImage", "inputs": {
                "images": ["8", 0], "filename_prefix": "agent_qwen_edit"}},
        }


TEMPLATES_QWEN = [QwenEdit()]
