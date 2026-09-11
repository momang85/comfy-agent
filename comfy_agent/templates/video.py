# -*- coding: utf-8 -*-
"""视频模板：MiniMax H3 文/图生视频、LTX-2.3 图生视频。

MiniMax H3 管线从本机 comfy_extras/nodes_minimax_h3.py 节点签名构建：
  UNETLoader(fl2va int8) + LoraLoaderModelOnly(turbo 8步)
  + CLIPLoader(qwen3vl, type=minimax) + VAELoader(video_vae)
  + MiniMaxH3ImageToVideo(prompt+首帧) / EmptyMiniMaxH3LatentAV(纯文生)
  + MiniMaxH3SigmaShift + CFGGuider + KSampler + VAEDecode + CreateVideo
"""
from __future__ import annotations

from .base import Param, Template

MM_UNET = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
MM_LORA = "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"
MM_CLIP = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
MM_VAE = "minimax_h3_video_vae_fp16.safetensors"

LTX_CKPT = "ltx-2.3-22b-distilled-1.1.safetensors"
LTX_LORA = "ltx-2.3-22b-distilled-lora-384-1.1.safetensors"


def _to_frames(raw, fps: float = 24.0) -> tuple:
    """把 length 归一为帧数。返回 (值, 是否做了秒→帧换算)。

    规则：'5s'/'5秒' → 按秒；裸值 <24（不足 1 秒，不可能是有效视频长度）
    → 按秒理解；其余按帧原样。"""
    s = str(raw).strip().lower()
    is_seconds = s.endswith("s") or s.endswith("秒")
    num = s.replace("秒", "").replace("s", "").strip()
    try:
        val = float(num)
    except ValueError:
        return raw, False
    if not is_seconds and val >= 24:
        return raw, False
    return max(24, int(round(val * fps))), True


class _LengthUnitsMixin:
    """视频 length 单位消歧（秒 vs 帧）。"""
    FPS = 24

    def normalize_params(self, p: dict) -> tuple[dict, list[str]]:
        out, notes = super().normalize_params(p)
        if out.get("length") is not None:
            frames, converted = _to_frames(out["length"], self.FPS)
            if converted:
                notes.append(f"length={out['length']!r} 按秒理解"
                             f"（{self.FPS}fps）→ {frames} 帧")
                out["length"] = frames
        return out, notes


class MiniMaxVideoBase(_LengthUnitsMixin, Template):
    category = "video"
    family = "minimax"
    models_used = [MM_UNET, MM_LORA, MM_CLIP, MM_VAE]
    est_vram_gb = 11.0
    est_minutes = "5-15"

    def common_params(self):
        return [
            Param("prompt", "str", "", "视频描述", required=True,
                  desc="自然语言描述（Qwen3VL 编码器，中文可用）。"
                       "要写动作/镜头/音效，不要写成逗号标签堆砌。"
                       "注意：本机链路目前只输出视频轨，提示词里的音效描述不会生成音频"),
            Param("width", "int", 768, "宽", minv=256, maxv=1344),
            Param("height", "int", 448, "高", minv=256, maxv=768),
            Param("length", "int", 124, "帧数", minv=5, maxv=3600,
                  unit="帧(24fps)",
                  aliases=["frames", "seconds", "duration", "num_frames",
                           "duration_s"],
                  desc="这是帧数不是秒数：124帧≈5秒；模型训练区间124-362。"
                       "传 seconds/duration=5 会自动按 24fps 换算成 120 帧"),
            Param("steps", "int", 8, "步数", minv=4, maxv=40,
                  desc="turbo LoRA 推荐 8 步"),
            Param("cfg", "float", 3.0, "CFG", minv=1.0, maxv=8.0,
                  desc="turbo 蒸馏模型推荐 3.0，调高（如 7+）容易过曝发糊"),
            Param("seed", "int", 0, "种子(0=随机)"),
        ]

    FPS = 24

    def render(self, p: dict):
        q = self._fill_defaults(p, self.params())
        return self.render_base(q, q.get("image") or "example.png")

    def render_base(self, q: dict, first_frame: str | None) -> dict:
        seed = q["seed"] or _rand_seed()
        wf = {
            # 模型链：UNET + turbo LoRA + SigmaShift
            "1": {"class_type": "UNETLoader",
                  "inputs": {"unet_name": MM_UNET, "weight_dtype": "default"}},
            "2": {"class_type": "LoraLoaderModelOnly", "inputs": {
                "lora_name": MM_LORA, "strength_model": 1.0,
                "model": ["1", 0]}},
            "3": {"class_type": "MiniMaxH3SigmaShift", "inputs": {
                "model": ["2", 0], "shift_video": 5.0, "shift_audio": 2.0}},
            # 文本编码器 + VAE
            "4": {"class_type": "CLIPLoader", "inputs": {
                "clip_name": MM_CLIP, "type": "minimax"}},
            "5": {"class_type": "VAELoader", "inputs": {"vae_name": MM_VAE}},
        }
        if first_frame:
            wf["10"] = {"class_type": "LoadImage", "inputs": {"image": first_frame}}
            wf["11"] = {"class_type": "MiniMaxH3ImageToVideo", "inputs": {
                "clip": ["4", 0], "vae": ["5", 0], "prompt": q["prompt"],
                "width": q["width"], "height": q["height"], "length": q["length"],
                "first_frame": ["10", 0]}}
            positive, latent = ["11", 0], ["11", 1]
        else:
            # 纯文生视频：EmptyMiniMaxH3LatentAV + CLIPTextEncode
            wf["11"] = {"class_type": "CLIPTextEncode", "inputs": {
                "clip": ["4", 0], "text": q["prompt"]}}
            wf["12"] = {"class_type": "EmptyMiniMaxH3LatentAV", "inputs": {
                "width": q["width"], "height": q["height"], "length": q["length"]}}
            positive, latent = ["11", 0], ["12", 0]
        # 负向条件（H3 无专用负向节点，用空文本编码）
        wf["13"] = {"class_type": "CLIPTextEncode", "inputs": {
            "clip": ["4", 0], "text": ""}}
        # 采样链（ComfyUI 0.33 GUIDER 模式）：
        #   CFGGuider→GUIDER；KSamplerSelect→SAMPLER；
        #   BasicScheduler→SIGMAS；RandomNoise→NOISE；
        #   SamplerCustomAdvanced 输出 LATENT
        wf["20"] = {"class_type": "CFGGuider", "inputs": {
            "model": ["3", 0], "positive": positive, "negative": ["13", 0],
            "cfg": q["cfg"]}}
        wf["21"] = {"class_type": "KSamplerSelect", "inputs": {
            "sampler_name": "euler"}}
        wf["22"] = {"class_type": "BasicScheduler", "inputs": {
            "model": ["3", 0], "scheduler": "simple", "steps": q["steps"],
            "denoise": 1.0}}
        wf["23"] = {"class_type": "RandomNoise", "inputs": {
            "noise_seed": seed}}
        wf["24"] = {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["23", 0], "guider": ["20", 0], "sampler": ["21", 0],
            "sigmas": ["22", 0], "latent_image": latent}}
        wf["25"] = {"class_type": "VAEDecode", "inputs": {
            "samples": ["24", 0], "vae": ["5", 0]}}
        wf["26"] = {"class_type": "CreateVideo", "inputs": {
            "images": ["25", 0], "fps": 24.0}}
        wf["27"] = {"class_type": "SaveVideo", "inputs": {
            "video": ["26", 0], "filename_prefix": "agent_minimax",
            "format": "auto"}}
        return wf


class MiniMaxT2V(MiniMaxVideoBase):
    id = "minimax_t2v"
    name = "文生视频-MiniMaxH3"
    desc = "文本直接生成音视频（Qwen3VL提示词+turbo 8步，支持中文）。"

    def params(self):
        return self.common_params()

    def render(self, p: dict):
        return self.render_base(self._fill_defaults(p, self.params()), None)


class MiniMaxI2V(MiniMaxVideoBase):
    id = "minimax_i2v"
    name = "图生视频-MiniMaxH3"
    desc = "上传首帧图+描述生成音视频（保持画面主体，让画面动起来）。"

    def params(self):
        return [Param("image", "image", "", "首帧图片", required=True)] + \
            self.common_params()

    def render(self, p: dict):
        q = self._fill_defaults(p, self.params())
        return self.render_base(q, q.get("image") or "example.png")


class LTXVideo(_LengthUnitsMixin, Template):
    """LTX-2.3 图生视频（对应用户 12GB 优化工作流的节点组合）。"""
    id = "ltx_i2v"
    name = "图生视频-LTX"
    category = "video"
    family = "ltx"
    desc = "LTX-2.3 图生视频（22B蒸馏版+384LoRA，本地Gemma文本编码器）。"
    models_used = [LTX_CKPT, LTX_LORA]
    est_vram_gb = 11.0
    est_minutes = "10-30"

    def params(self):
        return [
            Param("image", "image", "", "首帧图片", required=True),
            Param("prompt", "str", "", "视频描述", required=True,
                  desc="自然语言/英文描述（Gemma 编码）"),
            Param("width", "int", 768, "宽", minv=256, maxv=1280),
            Param("height", "int", 512, "高", minv=256, maxv=768),
            Param("length", "int", 121, "帧数", minv=9, maxv=257,
                  unit="帧(24fps)",
                  aliases=["frames", "seconds", "duration", "num_frames",
                           "duration_s"],
                  desc="帧数不是秒数：121帧≈5秒；传 seconds/duration=5 会自动换算"),
            Param("steps", "int", 10, "步数", minv=4, maxv=40),
            Param("cfg", "float", 3.0, "CFG", minv=1.0, maxv=10.0,
                  desc="蒸馏模型推荐 3.0 左右"),
            Param("seed", "int", 0, "种子(0=随机)"),
        ]

    def render(self, p: dict):
        q = self._fill_defaults(p, self.params())
        seed = q["seed"] or _rand_seed()
        return {
            "1": {"class_type": "LoadImage", "inputs": {"image": q["image"] or "example.png"}},
            "2": {"class_type": "CheckpointLoaderSimple",
                  "inputs": {"ckpt_name": LTX_CKPT}},
            "3": {"class_type": "LoraLoaderModelOnly", "inputs": {
                "lora_name": LTX_LORA, "strength_model": 1.0,
                "model": ["2", 0]}},
            "4": {"class_type": "CLIPTextEncode", "inputs": {
                "clip": ["2", 1], "text": q["prompt"]}},
            "5": {"class_type": "LTXVImgToVideo", "inputs": {
                "positive": ["4", 0], "negative": ["4", 0],
                "vae": ["2", 2], "width": q["width"], "height": q["height"],
                "length": q["length"], "batch_size": 1, "strength": 1.0,
                "image": ["1", 0]}},
            "6": {"class_type": "CFGGuider", "inputs": {
                "model": ["3", 0], "positive": ["5", 0], "negative": ["5", 1],
                "cfg": q["cfg"]}},
            "7": {"class_type": "KSamplerSelect", "inputs": {
                "sampler_name": "euler"}},
            "8": {"class_type": "BasicScheduler", "inputs": {
                "model": ["3", 0], "scheduler": "simple", "steps": q["steps"],
                "denoise": 1.0}},
            "9": {"class_type": "RandomNoise", "inputs": {
                "noise_seed": seed}},
            "10": {"class_type": "SamplerCustomAdvanced", "inputs": {
                "noise": ["9", 0], "guider": ["6", 0], "sampler": ["7", 0],
                "sigmas": ["8", 0], "latent_image": ["5", 2]}},
            "11": {"class_type": "VAEDecode", "inputs": {
                "samples": ["10", 0], "vae": ["2", 2]}},
            "12": {"class_type": "CreateVideo", "inputs": {
                "images": ["11", 0], "fps": 24.0}},
            "13": {"class_type": "SaveVideo", "inputs": {
                "video": ["12", 0], "filename_prefix": "agent_ltx",
                "format": "auto"}},
        }


class MergeVideos(Template):
    """合并多段视频（本机蓝图《Merge Videos》模式转模板）。

    输入视频须先 upload_image 送入 /input。当前版本不合并音轨
    （两个视频的音频会丢失），输出 24fps 合并视频。"""
    id = "merge_videos"
    name = "视频合并"
    category = "video"
    family = "minimax"
    desc = "把两段视频帧序列拼接合并为一个视频（多段续接的收尾步骤）"
    models_used = []
    est_vram_gb = 2.0
    est_minutes = "1-2"

    def params(self):
        return [
            Param("video1", "str", "", "第一段视频（/input 中的文件名）",
                  required=True),
            Param("video2", "str", "", "第二段视频（/input 中的文件名）",
                  required=True),
            Param("fps", "float", 24.0, "输出帧率"),
        ]

    def render(self, p: dict):
        q = self._fill_defaults(p, self.params())
        if not q.get("video1") or not q.get("video2"):
            raise ValueError("merge_videos 需要 video1 和 video2 两个视频文件")
        return {
            "1": {"class_type": "LoadVideo",
                  "inputs": {"file": q["video1"]}},
            "2": {"class_type": "LoadVideo",
                  "inputs": {"file": q["video2"]}},
            "3": {"class_type": "GetVideoComponents", "inputs": {
                "video": ["1", 0]}},
            "4": {"class_type": "GetVideoComponents", "inputs": {
                "video": ["2", 0]}},
            "5": {"class_type": "BatchImagesNode", "inputs": {
                # 动态输入（AUTOGROW）API 格式：输入名.前缀+序号
                "images.image0": ["3", 0], "images.image1": ["4", 0]}},
            "6": {"class_type": "CreateVideo", "inputs": {
                "images": ["5", 0], "fps": q["fps"]}},
            "7": {"class_type": "SaveVideo", "inputs": {
                "video": ["6", 0], "filename_prefix": "agent_merged",
                "format": "auto"}},
        }


class ExtractFrame(Template):
    """从视频提取指定帧（本机蓝图《Get Any Video Frame》模式简化版）。

    输入视频须先 upload_image 送入 /input；末帧索引 = 帧数-1
    （124 帧视频的末帧 = 123）。"""
    id = "extract_frame"
    name = "视频取帧"
    category = "video"
    family = "minimax"
    desc = "从视频提取指定帧保存为图片（多段续接取末帧用）"
    models_used = []
    est_vram_gb = 2.0
    est_minutes = "1"

    def params(self):
        return [
            Param("video", "str", "", "视频文件（/input 中的文件名）",
                  required=True),
            Param("frame_index", "int", 123, "帧索引（末帧=帧数-1）",
                  minv=0, maxv=10000),
        ]

    def render(self, p: dict):
        q = self._fill_defaults(p, self.params())
        if not q.get("video"):
            raise ValueError("extract_frame 需要 video 参数")
        return {
            "1": {"class_type": "LoadVideo", "inputs": {"file": q["video"]}},
            "2": {"class_type": "GetVideoComponents", "inputs": {
                "video": ["1", 0]}},
            "3": {"class_type": "ImageFromBatch", "inputs": {
                "image": ["2", 0], "batch_index": q["frame_index"],
                "length": 1}},
            "4": {"class_type": "SaveImage", "inputs": {
                "images": ["3", 0], "filename_prefix": "agent_frame"}},
        }


def _rand_seed():
    import secrets
    return secrets.randbelow(2**31 - 1) + 1


TEMPLATES_VIDEO = [MiniMaxT2V(), MiniMaxI2V(), LTXVideo(),
                   MergeVideos(), ExtractFrame()]
