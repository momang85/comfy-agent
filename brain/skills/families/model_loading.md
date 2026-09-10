# 模型加载速查（本机实测节点）

> 生成于 2026-09-10，数据源 = 本机 object_info 快照（1527 节点）+ 实景档案。所有类名经存在性断言。

## 核心节点（本机存在）
- **CheckpointLoaderSimple** — 加载预训练的扩散模型检查点，包含模型、CLIP文本编码器和VAE解码器。（关键参数: ckpt_name）
  - 坑: 确保模型文件已正确放置在ComfyUI的models/checkpoints目录下
- **CheckpointLoader** — 加载模型检查点及其配置（已弃用）（关键参数: config_name, ckpt_name）
  - 坑: 已弃用，建议使用CheckpointLoaderSimple替代
- **UNETLoader** — 加载扩散模型（UNet）（关键参数: unet_name, weight_dtype）
  - 坑: 权重数据类型选择不当可能导致内存溢出或精度损失
- **CLIPLoader** — 加载CLIP文本编码模型（关键参数: clip_name, type, device）
  - 坑: CLIP类型与基础模型不匹配会导致生成失败
- **DualCLIPLoader** — 加载双CLIP文本编码模型（如SDXL）（关键参数: clip_name1/clip_name2, type, device）
  - 坑: 两个CLIP类型不一致会导致文本编码错误
- **VAELoader** — 加载指定的VAE模型文件（关键参数: vae_name）
  - 坑: 选错VAE可能导致图像色彩异常或细节丢失
- **CLIPVisionLoader** — 加载CLIP视觉模型，用于图像特征提取（关键参数: clip_name, 通常从下拉菜单中选择预定义模型）
  - 坑: 未安装对应CLIP模型会导致加载失败
- **IPAdapterModelLoader** — 加载自定义IPAdapter模型文件（关键参数: ipadapter_file）
  - 坑: 文件路径需正确指向.safetensors或.pt文件
- **UpscaleModelLoader** — 加载图像放大模型（如ESRGAN等）（关键参数: model_name）
  - 坑: 未安装对应模型会导致加载失败，需提前下载
- **ControlNetLoader** — 加载ControlNet模型（关键参数: control_net_name）
  - 坑: ControlNet类型与任务不匹配会导致控制效果差
- **LoraLoader** — 加载LoRA模型并应用到基础模型和CLIP（关键参数: model, clip, lora_name, strength_model/strength_clip）
  - 坑: LoRA强度过高可能导致生成结果失真
- **LoraLoaderModelOnly** — 仅加载LoRA到模型（不加载CLIP）（关键参数: model, lora_name, strength_model）
  - 坑: 过高强度可能导致生成异常

## 惯例与骨架
- 管线共享一个 CheckpointLoaderSimple：引擎 compose 会自动合并重复加载器
- VAE 优先用 checkpoint 槽 2（自带 VAE），避免 SD1.5/SDXL VAE 族错配
- LoraLoaderModelOnly 只改模型不连 CLIP；LoRA 权重叠加 ≤1.2（角色 0.7-0.9）

---
本文档由 `scripts/rebuild_family_docs.py` 生成；节点库变动后重跑：`python scripts/rebuild_family_docs.py`
