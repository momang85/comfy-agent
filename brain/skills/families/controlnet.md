# ControlNet 控制速查（本机实测节点）

> 生成于 2026-09-10，数据源 = 本机 object_info 快照（1527 节点）+ 实景档案。所有类名经存在性断言。

## 核心节点（本机存在）
- **Canny** — 使用Canny算法检测图像边缘（关键参数: image, low_threshold, high_threshold）
  - 坑: 阈值设置过高可能导致边缘缺失，过低则噪声过多
- **OpenposePreprocessor** — 使用OpenPose模型检测人体姿态、面部和手部关键点（关键参数: image, detect_hand, detect_body, detect_face）
  - 坑: 检测手部需要更高分辨率；多人姿态检测可能混淆关键点
- **DWPreprocessor** — 估计人体姿态（手、身体、面部），用于姿态控制（关键参数: image, bbox_detector, pose_estimator）
  - 本机接线: image ← LoadImage
  - 坑: 检测多个部位会增加计算量，建议按需启用
- **DepthAnythingPreprocessor** — 使用Depth Anything估计深度图（关键参数: image, ckpt_name, resolution）
  - 坑: 小物体深度估计可能不准确
- **LineartStandardPreprocessor** — 标准线条提取，支持高斯模糊和强度阈值（关键参数: image, guassian_sigma, intensity_threshold, resolution）
  - 坑: 参数设置不当可能导致线条过粗或过细
- **ScribblePreprocessor** — 将图像转换为涂鸦风格的线条图（关键参数: image, resolution）
  - 坑: 复杂图像可能需要调整分辨率以获得更好的线条效果
- **HEDPreprocessor** — 提取软边缘线条（HED算法）（关键参数: image, safe, resolution）
  - 坑: safe模式可能降低边缘细节
- **PiDiNetPreprocessor** — 使用PiDiNet模型提取软边缘线条（关键参数: image, safe, resolution）
  - 坑: safe模式可能丢失细节；线条提取对对比度敏感
- **ControlNetLoader** — 加载ControlNet模型（关键参数: control_net_name）
  - 坑: ControlNet类型与任务不匹配会导致控制效果差
- **ControlNetApply** — （已弃用）应用ControlNet到条件编码（关键参数: conditioning, control_net, image, strength）
  - 坑: 已弃用，建议使用ControlNetApplyAdvanced
- **ControlNetApplyAdvanced** — 高级应用ControlNet到正负条件编码（关键参数: positive/negative, control_net, image, strength）
  - 本机接线: positive ← CLIPTextEncode; negative ← CLIPTextEncode; control_net ← ControlNetLoader
  - 坑: 控制范围设置不当可能导致控制效果失效
- **AIO_Preprocessor** — 集成多种预处理功能，通过单一节点切换不同预处理模式（关键参数: image, preprocessor, resolution）
  - 坑: 不同预处理模式效果差异大，需根据任务选择合适的类型
- **AnimalPosePreprocessor** — 估计动物姿态（基于AP10K数据集）（关键参数: image, bbox_detector, pose_estimator, resolution）
  - 坑: 仅支持动物姿态，对人类无效
- **AnimeFace_SemSegPreprocessor** — 预处理动漫面部图像进行语义分割（关键参数: image, remove_background_using_abg, resolution）
  - 坑: 非动漫图像可能导致分割错误

## 惯例与骨架
- 强度惯例：canny 0.6-0.8（end_percent 0.6-1.0 提前释放细节）；openpose 0.7-1.0 全程；depth 0.6-0.9
- 12GB 一次挂 1-2 个 CN；本机 SDXL 用 union promax 一模型多模式
- Canny 是核心节点离线可用；线稿/涂鸦/深度预处理器可能需联网下载模型

---
本文档由 `scripts/rebuild_family_docs.py` 生成；节点库变动后重跑：`python scripts/rebuild_family_docs.py`
