# 检测与细化速查（本机实测节点）

> 生成于 2026-09-10，数据源 = 本机 object_info 快照（1527 节点）+ 实景档案。所有类名经存在性断言。

## 核心节点（本机存在）
- **FaceDetailer** — 对图像中的人脸区域进行细节增强和修复（关键参数: image, model, clip, vae）
  - 坑: 未在本机工作流中使用，具体接线方式需参考实际需求
- **FaceDetailerPipe** — 通过管道配置对人脸进行细节增强和修复（关键参数: image, detailer_pipe, guide_size, max_size）
  - 本机接线: image ← VAEDecode; detailer_pipe ← EditDetailerPipe; guide_size ← PrimitiveFloat
  - 坑: 参数较多，需注意denoise和feather的平衡，避免过度修复
- **BboxDetectorCombined_v2** — 使用组合边界框检测器检测图像中的物体边界框（关键参数: bbox_detector, image, threshold, dilation）
  - 坑: threshold过高可能导致漏检，过低可能导致误检，需根据场景调整
- **SegmDetectorCombined_v2** — 使用组合分割检测器生成图像中物体的分割掩码（关键参数: segm_detector, image, threshold, dilation）
  - 坑: threshold和dilation参数需根据检测器特性和图像质量调整，否则可能影响分割精度
- **CLIPSegDetectorProvider** — 基于CLIPSeg模型提供文本驱动的分割检测功能（关键参数: text, blur, threshold, dilation_factor）
  - 坑: 阈值设置过高可能导致漏检，过低则产生过多噪声
- **ONNXDetectorProvider** — 加载ONNX格式的目标检测模型（关键参数: model_name）
  - 坑: 需确保模型文件与输入图像尺寸匹配
- **BboxDetectorSEGS** — 使用边界框检测器生成SEGS分割数据（关键参数: bbox_detector, image, threshold, dilation）
  - 坑: threshold过高可能导致漏检，过低则产生过多假阳性
- **SegmDetectorSEGS** — 使用分割检测器生成SEGS分割数据（关键参数: segm_detector, image, threshold, dilation）
  - 坑: dilation值过大会导致分割区域过度膨胀
- **SAMLoader** — 加载SAM（Segment Anything Model）分割模型（关键参数: model_name, device_mode）
  - 坑: 在无GPU环境下选择'Prefer GPU'会导致报错
- **SAMDetectorCombined** — 使用SAM模型进行组合式目标检测（关键参数: sam_model, segs, image, detection_hint）
  - 坑: threshold设置过低会产生过多小区域
- **SAMPreprocessor** — 使用SAM模型对图像进行语义分割（关键参数: image, resolution）
  - 坑: 分辨率设置过高可能导致处理速度变慢
- **SEGSPreview** — 预览SEGS数据，支持透明度模式（关键参数: segs, alpha_mode, min_alpha, fallback_image_opt）
  - 坑: alpha_mode和min_alpha参数影响预览效果
- **ToDetailerPipe** — 将基础模型配置转换为详情管道配置（关键参数: model, clip, vae, positive）
  - 本机接线: model ← DifferentialDiffusion; clip ← CheckpointLoaderSimple; vae ← ImpactSwitch
  - 坑: 需确保模型、CLIP和VAE兼容，避免版本不匹配导致错误
- **ToDetailerPipeSDXL** — 将SDXL基础模型配置转换为详情管道配置（关键参数: model, clip, vae, positive）
  - 坑: 未在本机工作流中使用，需注意SDXL的双模型配置复杂性

## 惯例与骨架
- FaceDetailer 默认：guide_size 512 / steps 20 / cfg 8 / denoise 0.5 / feather 5 / noise_mask on；小脸调 bbox_threshold 0.2
- 本机缺 face_yolov8m（只有手部模型）：FaceDetailer 的 bbox 检测不可用，可走 CLIPSegDetectorProvider/SAM(sam_vit_b) 检测链；装 face_yolov8m.pt 后启用完整修脸

---
本文档由 `scripts/rebuild_family_docs.py` 生成；节点库变动后重跑：`python scripts/rebuild_family_docs.py`
