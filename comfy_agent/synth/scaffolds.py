# -*- coding: utf-8 -*-
"""确定性骨架构造：给空图搭标准骨架（不借模板，add_node+connect 现场拼）。

骨架 = 从零建图的起点，之后大脑用 propose_edit 逐环节加料。
"""
from __future__ import annotations

from .graph import Graph
from .ops import add_node, connect, set_input

_CKPT = "sdXL\\novaAnimeXL_ilV180.safetensors"


def scaffold_t2i(knowledge, ckpt: str = None, width: int = 1024,
                 height: int = 1024) -> Graph:
    """文生图骨架：ckpt→clip±/latent→ksampler→vae→save。"""
    g = Graph({}, knowledge)
    add_node(g, "1", "CheckpointLoaderSimple",
             inputs={"ckpt_name": ckpt or _CKPT})
    add_node(g, "2", "CLIPTextEncode", inputs={"text": "masterpiece, best quality"})
    add_node(g, "3", "CLIPTextEncode", inputs={"text": "worst quality, low quality"})
    add_node(g, "4", "EmptyLatentImage",
             inputs={"width": width, "height": height, "batch_size": 1})
    add_node(g, "5", "KSampler", inputs={"steps": 20, "cfg": 7.0,
                                         "sampler_name": "dpmpp_2m",
                                         "scheduler": "simple", "denoise": 1.0})
    add_node(g, "6", "VAEDecode")
    add_node(g, "7", "SaveImage", inputs={"filename_prefix": "agent_synth"})
    connect(g, "1", 1, "2", "clip")
    connect(g, "1", 1, "3", "clip")
    connect(g, "1", 0, "5", "model")
    connect(g, "1", 2, "6", "vae")
    connect(g, "2", 0, "5", "positive")
    connect(g, "3", 0, "5", "negative")
    connect(g, "4", 0, "5", "latent_image")
    connect(g, "5", 0, "6", "samples")
    connect(g, "6", 0, "7", "images")
    return g


def scaffold_i2i(knowledge, ckpt: str = None, image: str = "example.png",
                 denoise: float = 0.6) -> Graph:
    """图生图骨架：LoadImage→VAEEncode 替代空潜空间。"""
    g = Graph({}, knowledge)
    add_node(g, "1", "CheckpointLoaderSimple",
             inputs={"ckpt_name": ckpt or _CKPT})
    add_node(g, "2", "CLIPTextEncode", inputs={"text": "masterpiece, best quality"})
    add_node(g, "3", "CLIPTextEncode", inputs={"text": "worst quality, low quality"})
    add_node(g, "4", "LoadImage", inputs={"image": image})
    add_node(g, "5", "VAEEncode")
    add_node(g, "6", "KSampler", inputs={"steps": 20, "cfg": 7.0,
                                         "sampler_name": "dpmpp_2m",
                                         "scheduler": "simple", "denoise": denoise})
    add_node(g, "7", "VAEDecode")
    add_node(g, "8", "SaveImage", inputs={"filename_prefix": "agent_synth"})
    connect(g, "1", 1, "2", "clip")
    connect(g, "1", 1, "3", "clip")
    connect(g, "1", 0, "6", "model")
    connect(g, "1", 2, "5", "vae")
    connect(g, "1", 2, "7", "vae")
    connect(g, "2", 0, "6", "positive")
    connect(g, "3", 0, "6", "negative")
    connect(g, "4", 0, "5", "pixels")
    connect(g, "5", 0, "6", "latent_image")
    connect(g, "6", 0, "7", "samples")
    connect(g, "7", 0, "8", "images")
    return g


SCAFFOLDS = {"t2i": scaffold_t2i, "i2i": scaffold_i2i}


def scaffold(knowledge, kind: str = "t2i", **kw) -> Graph:
    if kind not in SCAFFOLDS:
        raise ValueError(f"未知骨架 {kind}（可用: {list(SCAFFOLDS)}）")
    return SCAFFOLDS[kind](knowledge, **kw)
