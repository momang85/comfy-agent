# 音频节点家族速查

```markdown
# ComfyUI 音频家族速查

## 通用接线模式
- **输入/输出**：音频节点多通过 `AUDIO` 端口传递音频数据（波形/文件路径），部分节点需 `MODEL` 或 `CONFIG` 初始化。
- **链路顺序**：生成（ByteDance/ElevenLabs）→ 处理（Trim/Equalizer）→ 合并（Join/Merge）→ 输出（Save/Preview）。

## 关键节点与参数
- **生成类**：  
  `ByteDanceSeedAudio`（文本→音频，需 prompt）、`ElevenLabsTextToSpeech`（需 voice_id）。  
- **处理类**：  
  `TrimAudioDuration`（start/end 秒）、`AudioAdjustVolume`（gain dB）、`AudioEqualizer3Band`（low/mid/high 增益）。  
- **输出类**：  
  `SaveAudio`（WAV）、`SaveAudioMP3`（quality 0-100）、`PreviewAudio`（实时播放）。

## 常见坑
1. **格式不匹配**：MP3/Opus 节点需确保输入为 PCM 数据，避免直接加载压缩文件。  
2. **通道错误**：`SplitAudioChannels` 输出单声道列表，`JoinAudioChannels` 需等长输入。  
3. **模型加载**：ElevenLabs 节点需提前配置 API Key，否则报 401 错误。

## 接口约定
- **上游**：文本生成（LLM）→ `TextToSpeech`；视频处理（VideoFrames）→ `AudioIsolation`。  
- **下游**：音频 → 视频合成（VideoCombine）、动画（AnimateDiff）需同步帧率。
```