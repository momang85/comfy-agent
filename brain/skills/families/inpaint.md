# 局部重绘与遮罩速查（本机实测节点）

> 生成于 2026-09-10，数据源 = 本机 object_info 快照（1527 节点）+ 实景档案。所有类名经存在性断言。

## 核心节点（本机存在）
- **VAEEncodeForInpaint** — 专为图像修复编码的VAE处理，支持掩码扩展（关键参数: pixels, vae, mask, grow_mask_by）
  - 坑: grow_mask_by参数过大可能导致修复区域模糊
- **SetLatentNoiseMask** — 为潜在图像设置噪声遮罩（关键参数: samples, mask）
  - 坑: 遮罩尺寸需要与潜在图像匹配
- **InpaintModelConditioning** — 为图像修复任务准备模型条件（关键参数: positive/negative, vae, pixels, mask）
  - 坑: 掩码区域需与实际修复区域一致
- **LoadImageMask** — 将图像转换为掩码（支持通道选择）（关键参数: image, channel）
  - 坑: 通道选择错误会导致掩码无效
- **MaskComposite** — 组合两个遮罩（关键参数: destination, source, x/y, operation）
  - 坑: 组合方式选择错误可能导致预期外的遮罩效果
- **GrowMask** — 扩大遮罩区域（关键参数: mask, expand, tapered_corners）
  - 坑: 扩大值过大可能导致遮罩形状失真
- **AILab_ReferenceLatentMask** — 基于参考遮罩扩展潜在空间的遮罩区域（关键参数: conditioning, latent, mask, expand）
  - 坑: expand值过大可能导致边缘模糊过度
- **WanFunInpaintToVideo** — 通过功能修复生成视频（未在本机工作流中使用）（关键参数: positive/negative, vae, width/height/length, batch_size）
  - 坑: 起始和结束图像需有明确的内容对应关系

## 惯例与骨架
- 标准链：LoadImage+Mask → VAEEncodeForInpaint(grow_mask_by 6) → KSampler(denoise 0.5-0.7) → 解码；提示词只描述遮罩区内容
- SDXL 小区域重绘可上 CropAndStitch（本机未装则整图重绘）

---
本文档由 `scripts/rebuild_family_docs.py` 生成；节点库变动后重跑：`python scripts/rebuild_family_docs.py`
