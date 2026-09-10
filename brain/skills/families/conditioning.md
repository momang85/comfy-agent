# 条件与提示词编码速查（本机实测节点）

> 生成于 2026-09-10，数据源 = 本机 object_info 快照（1527 节点）+ 实景档案。所有类名经存在性断言。

## 核心节点（本机存在）
- **CLIPTextEncode** — 将文本提示词编码为CLIP模型嵌入向量，用于引导扩散模型生成特定图像（关键参数: text, clip）
  - 本机接线: clip ← CheckpointLoaderSimple; text ← ImpactWildcardProcessor; clip ← Power Lora Loader (rgthree)
  - 坑: 确保CLIP模型与使用的扩散模型兼容（如SD1.5/SD2.0/SDXL需要不同CLIP）
- **CLIPTextEncodeSDXL** — 为SDXL基础模型编码提示文本（关键参数: clip, width/height, crop_w/crop_h, target_width/target_height）
  - 坑: 需使用SDXL专用的CLIP编码器，text_g和text_l需配合使用
- **ConditioningCombine** — 合并两个条件向量（关键参数: conditioning_1/conditioning_2）
  - 坑: 合并后的条件向量维度可能需要调整
- **ConditioningConcat** — 将两个条件向量在特征维度上拼接（关键参数: conditioning_to, conditioning_from）
  - 坑: 需要确保两个条件向量的特征维度兼容
- **ConditioningAverage** — 将两个条件向量进行加权平均融合（关键参数: conditioning_to, conditioning_from, conditioning_to_strength）
  - 坑: 权重设置不当可能导致条件特征丢失
- **ConditioningSetTimestepRange** — 设置条件的时间步范围（关键参数: conditioning, start, end）
  - 坑: 范围设置不当可能影响生成效果
- **ConditioningZeroOut** — 清空条件信息，保留结构（关键参数: conditioning）
  - 坑: 清空后条件将不提供任何指导信息
- **ConditioningSetArea** — 设置条件向量的作用区域（绝对坐标）（关键参数: conditioning, width/height/x/y, strength）
  - 坑: 区域坐标超出图像范围可能导致无效设置
- **ARVideoI2V** — 图像到视频的自回归生成（关键参数: model, vae, start_image, width/height）
  - 坑: 起始图像质量直接影响视频生成效果
- **ArgosTranslateCLIPTextEncodeNode** — 将文本翻译为目标语言并编码为CLIP向量，实现跨语言生成控制（关键参数: from_translate, to_translate, text, clip）
  - 坑: 小众语言翻译质量可能不稳定，建议使用主流语言
- **AudioEncoderEncode** — 将音频输入编码为条件数据（关键参数: audio_encoder）
  - 坑: 音频格式不匹配可能导致编码失败
- **BerniniConditioning** — 基于参考视频和图像生成条件（关键参数: positive）
  - 坑: 未在本机工作流中使用，参考图像的自动增长功能可能导致内存问题
- **CLIPSetLastLayer** — 动态设置CLIP模型的编码层数，用于控制文本条件的粒度（关键参数: clip, stop_at_clip_layer）
  - 本机接线: clip ← CheckpointLoaderSimple
  - 坑: stop_at_clip_layer设置过小可能导致生成内容偏离文本描述
- **CLIPTextEncodeControlnet** — 为ControlNet编码文本条件（关键参数: clip）
  - 坑: 文本与ControlNet类型不匹配可能导致控制失效

## 惯例与骨架
- 正/负分开两个 CLIPTextEncode 编码，分别接 KSampler 正负
- 超 77 token：拆两个编码器 + ConditioningCombine（每个各有独立预算；主构图放第一个）
- 别写 A1111 的 [a:b:0.5]（ComfyUI 用 ConditioningSetTimestepRange）
- 正向里不写否定词（no hat/without）——CLIP 不做否定

---
本文档由 `scripts/rebuild_family_docs.py` 生成；节点库变动后重跑：`python scripts/rebuild_family_docs.py`
