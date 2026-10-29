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
3. QwenImageDiffsynthControlnet（fun controlnet union 结构控制）**把 RGB 原图直接
   当控制图会出花屏**（实测：矩形被抹成灰块、出现乱码纹理）。正确接法是喂
   **结构图**——本机用 Core 的 Canny 从原图提边缘再进补丁，strength=0.7 时
   结构完全保住、材质可以随便换（实测：红圆 → 金属蓝圆盘，边缘与原图重合）。
   strength=1.0 + end_percent=0.5 会放出乱码文字，故固定 start=0/end=1.0。
   `structure="canny"` 才接这条链，默认 off。
4. cfg=1.0、steps=25、res_multistep+simple、ModelSamplingAuraFlow(shift=3,
   sampling="flow") 是本机官方工作流验证过的组合，原样沿用。
5. 局部改图走"裁剪 → 重绘 → 贴回"：ImageCrop 抠出 box 喂编码器，重绘结果用
   ImageScale 压回 box 尺寸，再 ImageCompositeMasked 贴回原图（resize_source
   必须 False——置 True 会把 source 缩放到**整张图**尺寸，几何全错）。
   全流程都在 ComfyUI 内部完成，不引入 Pillow，保持零第三方依赖。
"""
from __future__ import annotations

from .base import Param, Template

QWEN_UNET = "qwen_image_2.1_int8_convrot.safetensors"
QWEN_TE = "qwen3vl_8b_int8_convrot.safetensors"
QWEN_VAE = "qwen_image_2.1_vae_bf16.safetensors"
# 结构控制补丁（本机已有，但 v1 的正确接法未验证，先不放进 models_used）
QWEN_PATCH = "qwen_image_2.1_fun_controlnet_union_int8_convrot.safetensors"

NEG_QWEN = "模糊, 变形, 低质量, 水印, 多余文字"


def _parse_region(text):
    """'x,y,w,h'（像素）→ (x, y, w, h)；留空或不合法返回 None。

    容忍中文逗号、空格、"x,y,w,h" 四段。沿用 ComfyUI ImageCrop 的语义：
    (x, y) 是左上角坐标，w/h 是裁剪宽高。
    """
    if text is None:
        return None
    s = str(text).strip().replace("，", ",").replace("；", ",").replace(";", ",")
    if not s:
        return None
    parts = [x for x in s.replace(",", " ").split() if x]
    if len(parts) != 4:
        return None
    try:
        x, y, w, h = (int(float(v)) for v in parts)
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0 or x < 0 or y < 0:
        return None
    return x, y, w, h


def _round32(v: int) -> int:
    return max(512, min(1536, int(v) // 32 * 32))



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
            Param("region", "str", "", "只改这一块",
                  desc="留空＝整图重绘。填「x,y,w,h」（原图像素，左上角+宽高）"
                       "就只重绘这一块，其余像素原样保留，如「100,300,256,256」"),
            Param("batch", "int", 1, "出图张数", minv=1, maxv=4,
                  desc="同一指令出几张（1-4）；局部改图固定 1 张"),
            Param("structure", "choice", "off", "结构控制",
                  choices=["off", "canny"],
                  desc="canny＝先用边缘图锁住原图结构再重绘（换材质/风格时形状不跑），"
                       "需要 qwen_image_2.1_fun_controlnet_union 补丁"),
            Param("structure_strength", "float", 0.7, "结构强度",
                  minv=0.3, maxv=1.5,
                  desc="仅 canny 生效；0.7 实测结构最稳，过高会出乱码文字"),
            Param("resolution", "int", 1024, "输出边长", minv=512, maxv=1536,
                  unit="px（32 的倍数）",
                  desc="输出为正方形，边长按 32 对齐；建议 1024。"
                       "局部改图留空时按裁剪块的边长自动取"),
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

    def models_used_for(self, p: dict) -> list[str]:
        """按参数给依赖：结构控制才需要 fun controlnet union 补丁。

        引擎的缺模型判定走它，所以选 off 的用户不会被要求下载补丁。
        """
        out = list(self.models_used)
        if str((p or {}).get("structure") or "off") == "canny":
            out.append(QWEN_PATCH)
        return out

    def render(self, p: dict):
        q = self._fill_defaults(p, self.params())
        region = _parse_region(q.get("region"))
        batch = max(1, min(4, int(q["batch"] or 1)))
        if region:
            # 局部改图：贴回是按 1 张算的，多张会互相覆盖
            batch = 1
            x, y, w, h = region
            if p.get("resolution") not in (None, "", 0):
                res = _round32(int(p["resolution"]))
            else:
                res = _round32(max(w, h) + 31)   # 向上取到 32 的倍数，保住细节
        else:
            res = _round32(int(q["resolution"] or 1024))
        seed = int(q["seed"] or 0)
        g = {
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
        }
        if region:
            g["10"] = {"class_type": "ImageCrop", "inputs": {
                "image": ["4", 0], "width": w, "height": h, "x": x, "y": y}}
            ref_img = ["10", 0]
        else:
            ref_img = ["4", 0]
        g["6"] = {"class_type": "TextEncodeQwenImage21", "inputs": {
            "clip": ["2", 0],
            "prompt": q["prompt"] or "",
            "negative_prompt": q["negative"] or "",
            "vae": ["3", 0],
            "resolution": res,
            "images": {"image_1": ref_img}}}
        latent_src = ["6", 2]
        if batch > 1:
            g["11"] = {"class_type": "RepeatLatentBatch", "inputs": {
                "samples": latent_src, "amount": batch}}
            latent_src = ["11", 0]
        model_src = ["5", 0]
        if str(q.get("structure") or "off") == "canny":
            # 结构图必须是边缘/深度这类控制图；直接喂 RGB 原图会花屏
            g["20"] = {"class_type": "Canny", "inputs": {
                "image": ref_img, "low_threshold": 0.4, "high_threshold": 0.8}}
            g["21"] = {"class_type": "ModelPatchLoader", "inputs": {
                "name": QWEN_PATCH}}
            g["22"] = {"class_type": "QwenImageDiffsynthControlnet", "inputs": {
                "model": ["5", 0], "model_patch": ["21", 0],
                "vae": ["3", 0], "image": ["20", 0],
                "strength": float(q["structure_strength"]
                                  if q["structure_strength"] is not None else 0.7),
                "start_percent": 0.0, "end_percent": 1.0}}
            model_src = ["22", 0]
        g["7"] = {"class_type": "KSampler", "inputs": {
            "model": model_src,
            "seed": seed,
            "steps": int(q["steps"] or 25),
            "cfg": float(q["cfg"] if q["cfg"] is not None else 1.0),
            "sampler_name": q["sampler"] or "res_multistep",
            "scheduler": q["scheduler"] or "simple",
            "positive": ["6", 0],
            "negative": ["6", 1],
            "latent_image": latent_src,
            "denoise": 1.0}}
        g["8"] = {"class_type": "VAEDecode", "inputs": {
            "samples": ["7", 0], "vae": ["3", 0]}}
        out = ["8", 0]
        if region:
            # 重绘结果是正方形，先压回裁剪块尺寸再原位贴回
            g["12"] = {"class_type": "ImageScale", "inputs": {
                "image": out, "upscale_method": "lanczos",
                "width": w, "height": h, "crop": "center"}}
            g["13"] = {"class_type": "ImageCompositeMasked", "inputs": {
                "destination": ["4", 0], "source": ["12", 0],
                "x": x, "y": y, "resize_source": False}}
            out = ["13", 0]
        g["9"] = {"class_type": "SaveImage", "inputs": {
            "images": out, "filename_prefix": "agent_qwen_edit"}}
        return g


TEMPLATES_QWEN = [QwenEdit()]
