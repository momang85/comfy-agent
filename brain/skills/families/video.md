# 视频速查（本机实测节点）

> 生成于 2026-09-10，数据源 = 本机 object_info 快照（1527 节点）+ 实景档案。所有类名经存在性断言。

## 核心节点（本机存在）
- **LoadVideo** — 从文件加载视频（未在本机工作流中使用）（关键参数: file）
  - 坑: 本工作流中未使用，需确认支持的文件格式
- **GetVideoComponents** — 解析视频文件为组件（未在本机工作流中使用）（关键参数: video）
  - 坑: 本工作流中未使用，实际功能需验证
- **ImageFromBatch** — 从图像批次中提取指定图像（关键参数: image, batch_index, length）
  - 坑: 索引超出范围会导致错误
- **BatchImagesNode** — 将多张图像合并为批次（关键参数: images）
  - 坑: 输入图像尺寸不一致可能导致批次处理失败
- **CreateVideo** — 将图像序列和音频合成为视频文件（关键参数: images, audio, fps, bit_depth）
  - 本机接线: images ← LTXVSeparateAVLatent; audio ← LTXVAudioVAEDecode; fps ← PrimitiveFloat
  - 坑: fps参数在本工作流中被设为1而非默认30，可能影响视频流畅度
- **SaveVideo** — 保存视频文件（支持多种格式）（关键参数: video）
  - 本机接线: video ← CreateVideo
  - 坑: 在《Image to Video》工作流中用于保存最终视频，需注意格式选择
- **VHS_VideoCombine** — 将图像序列合成为视频或GIF（关键参数: images）
  - 坑: 帧率设置不当可能导致视频卡顿或过快；格式选择需考虑兼容性和文件大小
- **MiniMaxH3ImageToVideo** — 将单帧图像扩展为 MiniMax H3 视频序列（关键参数: clip）
  - 坑: 未在本机工作流中使用，需确保首末帧图像尺寸一致
- **EmptyMiniMaxH3LatentAV** — 创建空的 MiniMax H3 潜在音频-视频张量（关键参数: width）
  - 坑: 未在本机工作流中使用，需确保分辨率与时长符合模型要求
- **MiniMaxH3SigmaShift** — 调整 MiniMax H3 模型的视频/音频采样偏移（关键参数: model）
  - 坑: 未在本机工作流中使用，偏移量需根据生成效果调整
- **LTXVImgToVideo** — 将图像扩展为 LTXV 视频序列（关键参数: positive/negative）
  - 坑: 未在本机工作流中使用，需确保图像尺寸与参数一致
- **BeebleSwitchXVideoEdit** — 使用Beeble SwitchX进行视频编辑（关键参数: video, prompt, alpha_mode, max_resolution）
  - 坑: 分辨率设置过高可能导致处理缓慢，未设置prompt可能无法进行有效编辑
- **BriaRemoveVideoBackground** — 移除视频背景（关键参数: video）
  - 坑: 动态背景可能导致分割不稳定
- **BriaTransparentVideoBackground** — 移除视频背景并生成透明通道（关键参数: video）
  - 坑: 透明边缘处理不当可能导致毛边现象

## 惯例与骨架
- MiniMax H3：turbo LoRA strength 1.0 + 8 步；24fps、124 帧≈5 秒；官方 16:9 = 1344x768
- LTX 蒸馏版：8 步低 CFG；帧数 8k+1 对齐
- 多段续接：extract_frame 取末帧 → 下一段 i2v → merge_videos 合并

---
本文档由 `scripts/rebuild_family_docs.py` 生成；节点库变动后重跑：`python scripts/rebuild_family_docs.py`
