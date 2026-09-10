# 潜空间速查（本机实测节点）

> 生成于 2026-09-10，数据源 = 本机 object_info 快照（1527 节点）+ 实景档案。所有类名经存在性断言。

## 核心节点（本机存在）
- **EmptyLatentImage** — 创建一个空的潜在图像张量，作为后续去噪过程的初始输入。（关键参数: width, height, batch_size）
  - 本机接线: width ← GetImageSize; height ← GetImageSize; batch_size ← GetImageSize
  - 坑: width 和 height 的值必须与 VAE 节点的分辨率设置一致，否则会导致解码失败。
- **LatentUpscale** — 按指定尺寸放大隐空间图像（关键参数: samples, upscale_method, width/height, crop）
  - 坑: crop设为center可能裁剪重要内容；算法选择不当会引入伪影
- **LatentUpscaleBy** — 按比例放大隐空间图像（关键参数: samples, upscale_method, scale_by）
  - 本机接线: samples ← KSampler
  - 坑: scale_by过大可能导致生成质量下降
- **LatentComposite** — 将两个潜在图像进行合成（关键参数: samples_to, samples_from, x/y, feather）
  - 坑: 偏移参数超出目标图像范围可能导致部分内容丢失
- **SetLatentNoiseMask** — 为潜在图像设置噪声遮罩（关键参数: samples, mask）
  - 坑: 遮罩尺寸需要与潜在图像匹配
- **VAEEncode** — 将像素图像编码为VAE隐空间表示（关键参数: pixels, vae）
  - 本机接线: pixels ← LoadImage; vae ← VAELoader
  - 坑: 输入图像尺寸需与VAE模型训练分辨率匹配，否则可能导致编码失真
- **VAEDecode** — 将潜在空间图像解码为像素空间图像（关键参数: samples (LATENT), vae (VAE)）
  - 本机接线: samples ← CheckpointLoaderSimple; vae ← ImpactSwitch; samples ← KSampler
  - 坑: 确保vae与采样时使用的模型一致，否则可能导致解码失败或图像异常
- **VAEDecodeTiled** — 分块解码潜在表示为图像，节省显存（关键参数: samples, vae, tile_size, overlap）
  - 坑: 过小的tile_size可能导致处理时间增加
- **LatentFromBatch** — 从批次LATENT中提取指定索引的样本（关键参数: samples, batch_index, length）
  - 坑: batch_index超出范围会导致无输出
- **BatchLatentsNode** — 将多个潜在表示合并为批次（关键参数: latents）
  - 坑: 输入潜在表示维度不一致可能导致批次处理失败
- **EmptyARVideoLatent** — 创建空的视频潜在空间（关键参数: width/height, length, batch_size）
  - 坑: 尺寸设置需与模型输入要求匹配
- **EmptyAceStep1.5LatentAudio** — 创建1.5版本空音频潜在张量（关键参数: seconds, batch_size）
  - 坑: batch_size大于1时需确保后续节点支持批量处理
- **EmptyAceStepLatentAudio** — 创建指定时长的空音频潜在张量（关键参数: seconds, batch_size）
  - 坑: seconds值过大可能导致内存不足
- **EmptyChromaRadianceLatentImage** — 创建Chroma Radiance模型的空潜空间图像（关键参数: width, height, batch_size）
  - 坑: 分辨率过高可能导致内存溢出；batch_size需根据显存调整

## 惯例与骨架
- hiresfix 标准链：KSampler → LatentUpscaleBy(1.5-2x, bicubic/bislerp) → KSampler(denoise 0.3-0.5, SD1.5 用 0.5-0.55)
- ≥1536² 解码必须 VAEDecodeTiled(tile 512/overlap 64)：12GB 卡 2048² 整图解码需 14GB+

---
本文档由 `scripts/rebuild_family_docs.py` 生成；节点库变动后重跑：`python scripts/rebuild_family_docs.py`
