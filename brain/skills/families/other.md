# 其他常用速查（本机实测节点）

> 生成于 2026-09-10，数据源 = 本机 object_info 快照（1527 节点）+ 实景档案。所有类名经存在性断言。

## 核心节点（本机存在）
- **ImageScaleToTotalPixels** — 按总像素数缩放图像（关键参数: image, upscale_method, megapixels, resolution_steps）
  - 坑: megapixels设置过高可能导致内存不足
- **ImpactLogger** — 记录调试数据，用于工作流调试和日志输出（关键参数: data, text）
  - 坑: 未在本机工作流中使用，具体功能需参考官方文档
- **ImpactLatentInfo** — 提取潜在空间的元数据信息（关键参数: value）
  - 坑: 不同模型潜在空间结构可能不同
- **ImpactImageInfo** — 提取图像的元数据信息（关键参数: value）
  - 坑: 对特殊格式图像可能解析不全
- **InpaintPreprocessor** — 预处理图像用于区域修复（关键参数: image, mask, black_pixel_for_xinsir_cn）
  - 坑: mask必须与图像尺寸一致
- **SAMPreprocessor** — 使用SAM模型对图像进行语义分割（关键参数: image, resolution）
  - 坑: 分辨率设置过高可能导致处理速度变慢

## 惯例与骨架
- ImpactWildcardEncode 支持通配符/LoRA 语法文本（见 logic.md）
- ImpactLogger 可调试中间值；ImpactLatentInfo/ImageInfo 打印张量信息

---
本文档由 `scripts/rebuild_family_docs.py` 生成；节点库变动后重跑：`python scripts/rebuild_family_docs.py`
