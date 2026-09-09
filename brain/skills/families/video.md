# 视频节点家族速查

```markdown
# ComfyUI 视频节点家族速查

## 通用接线模式
- **输入**：视频文件/帧序列 → 处理节点 → 输出视频/帧
- **核心流**：`LoadVideo` → [帧处理/增强] → `SaveVideo`/`CreateVideo`
- **数据集**：`LoadVideoDataSetFromFolder` → `ShuffleVideoDataset` → 训练/采样

## 关键节点与参数
- **LoadVideo**：支持MP4/WEBM，`start_frame`/`end_frame`裁剪
- **VideoFrameSample**：`frame_rate`降帧，`every_N`抽帧
- **Flux3TextToVideoNode**：`prompt`控制生成，`motion_scale`调节动态强度
- **FrameInterpolate**：`interpolation_factor`（2-8倍插帧）
- **SaveVideo**：`fps`/`quality`（0-100）控制输出质量

## 常见坑
1. **内存溢出**：长视频用`VideoTemporalCrop`分块处理
2. **帧率错位**：`LoadVideo`后检查`fps`与节点输入是否匹配
3. **数据集乱序**：`ShuffleVideoDataset`需配合`seed`参数复现结果

## 接口约定
- **上游**：CLIP文本编码器 → `Flux3TextToVideoNode`（文本转视频）
- **下游**：VAE解码器 → `VideoFrameSample`（帧处理）→ `SaveVideo`
- **音频**：`Audio`节点家族通过`CreateVideo`的`audio_path`参数合成

> 注：视频生成节点（如Flux系列）需确保GPU显存充足，建议分≤16秒片段处理。
```