# 图像读写与变换速查（本机实测节点）

> 生成于 2026-09-10，数据源 = 本机 object_info 快照（1527 节点）+ 实景档案。所有类名经存在性断言。

## 核心节点（本机存在）
- **LoadImage** — 从文件系统加载图像作为输入源（关键参数: image）
  - 坑: 选错文件路径会导致加载失败
- **LoadImageOutput** — 从历史输出中重新加载图像（关键参数: image）
  - 坑: 选择非图像类型的输出会报错
- **LoadImageMask** — 将图像转换为掩码（支持通道选择）（关键参数: image, channel）
  - 坑: 通道选择错误会导致掩码无效
- **SaveImage** — 将生成的图像保存到 ComfyUI 的输出目录中。（关键参数: 1. images）
  - 本机接线: images ← FaceDetailerPipe; images ← VAEDecode
  - 坑: 文件名前缀如果包含特殊字符或路径分隔符，可能导致保存失败。
- **PreviewImage** — 实时预览VAE解码后的图像（关键参数: images）
  - 本机接线: images ← VAEDecode
  - 坑: 频繁调用可能影响性能，建议仅在调试时使用
- **ImageScaleBy** — 按比例放大像素图像（关键参数: image, upscale_method, scale_by）
  - 坑: scale_by过大可能导致图像模糊
- **ImageScaleToTotalPixels** — 按总像素数缩放图像（关键参数: image, upscale_method, megapixels, resolution_steps）
  - 坑: megapixels设置过高可能导致内存不足
- **ImageFromBatch** — 从图像批次中提取指定图像（关键参数: image, batch_index, length）
  - 坑: 索引超出范围会导致错误
- **BatchImagesNode** — 将多张图像合并为批次（关键参数: images）
  - 坑: 输入图像尺寸不一致可能导致批次处理失败
- **ImageBlend** — 混合两张图像（关键参数: image1, image2, blend_factor, blend_mode）
  - 坑: 混合因子过高或过低可能导致混合不自然
- **ImageUpscaleWithModel** — 使用模型对图像进行放大处理（关键参数: upscale_model, image）
  - 本机接线: upscale_model ← UpscaleModelLoader; image ← UltimateSDUpscale
  - 坑: 放大模型需与任务匹配，否则效果不佳

## 惯例与骨架
- LoadImage 的槽 0=IMAGE 槽 1=MASK（按 alpha）；遮罩白色=生效区
- 纯像素放大用 ImageScaleBy（lanczos）；模型放大用 ImageUpscaleWithModel（本机未装 ESRGAN 文件时不可用）

---
本文档由 `scripts/rebuild_family_docs.py` 生成；节点库变动后重跑：`python scripts/rebuild_family_docs.py`
