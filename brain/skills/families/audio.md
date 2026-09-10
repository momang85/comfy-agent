# 音频速查（本机实测节点）

> 生成于 2026-09-10，数据源 = 本机 object_info 快照（1527 节点）+ 实景档案。所有类名经存在性断言。

## 核心节点（本机存在）
- **LoadAudio** — 从文件系统加载音频（关键参数: audio）
  - 坑: 文件路径必须存在且格式受支持
- **SaveAudio** — 将音频保存为FLAC格式（已弃用）（关键参数: audio, filename_prefix）
  - 坑: 已弃用，建议使用SaveAudioAdvanced
- **SaveAudioMP3** — 将音频保存为MP3格式（已弃用）（关键参数: audio, filename_prefix, quality）
  - 坑: 已弃用，建议使用SaveAudioAdvanced
- **PreviewAudio** — 在UI中预览音频（关键参数: audio）
  - 坑: 预览功能依赖浏览器支持
- **SplitAudioChannels** — 将立体声音频分离为左右单声道（关键参数: audio）
  - 坑: 仅对立体声有效，单声道输入无效果
- **TrimAudioDuration** — 裁剪音频指定时间段（关键参数: audio, start_index, duration）
  - 坑: start_index + duration不能超过音频总长度
- **MergeTextLists** — 合并多个文本列表（已弃用）（关键参数: texts）
  - 坑: 节点已标记为DEPRECATED，建议使用ListConcatenate节点
- **AudioAdjustVolume** — 调整音频音量（关键参数: audio, volume）
  - 坑: volume值过高可能导致削波失真
- **AudioConcat** — 将两个音频片段按指定方向拼接（关键参数: audio1, audio2, direction）
  - 坑: 拼接时需确保两个音频采样率一致，否则可能导致播放异常
- **AudioEqualizer3Band** — 三段音频均衡器调节低中高频（关键参数: audio, low_gain_dB/low_freq, mid_gain_dB/mid_freq/mid_q, high_gain_dB/high_freq）
  - 坑: Q值过高可能导致频段调节过于尖锐
- **AudioMerge** — 按指定方法合并两个音频信号（关键参数: audio1, audio2, merge_method）
  - 坑: 合并方法选择不当可能导致音量过大或失真
- **ByteDanceSeedAudio** — 根据文本提示生成语音（关键参数: text_prompt, reference_mode, sample_rate, speech_rate）
  - 坑: 模型选择可能影响多语言支持效果
- **ConditioningStableAudio** — 为Stable Audio模型生成条件信息（关键参数: positive, negative, seconds_start, seconds_total）
  - 坑: 时间参数需与音频生成时长匹配
- **ElevenLabsAudioIsolation** — 隔离音频中的语音（关键参数: audio）
  - 坑: 背景噪声复杂时效果可能不佳

## 惯例与骨架
- MiniMax H3 音画同生：音效写进提示词句尾（"with crisp glass cutting sounds"），不需要音频节点
- 独立音频处理走 VHS 节点；音频 A/B 拼接注意采样率一致

---
本文档由 `scripts/rebuild_family_docs.py` 生成；节点库变动后重跑：`python scripts/rebuild_family_docs.py`
