# 视频生成（minimax_t2v / minimax_i2v / ltx_i2v）

## 12GB 显存调度（RTX 5070 Ti Laptop 实测环境）
- 全部视频模板预估 ~11GB：执行前确保无其他任务占显存
- 分辨率×时长是显存主因：768x448 起步，成功后再加码
- OOM 时优先降分辨率（→512x320），其次降帧数

## 模板选择
- 纯文字生成视频（可中文）→ `minimax_t2v`（Qwen3VL 编码器+turbo 8步）
- 有首帧图让画面动起来 → `minimax_i2v`（MiniMax H3）
  （注意：本机链路目前只输出**视频轨**，提示词里的音效描述不会生成音频；
  要音频需把 `VAEDecodeAudio` 接到 `CreateVideo` 的 audio 输入，尚未验证）
- 首帧图+更电影感（英文提示词强）→ `ltx_i2v`（22B 蒸馏+本地 Gemma）

## 提示词
- minimax 家族：中/英自然语言。结构=主体+场景+动作+镜头（推/拉/摇/跟随）+音效。
  例："一只橘猫戴着宇航头盔漂浮在太空舱，爪子轻拨按钮，镜头缓慢推近，舷窗阳光，背景仪器嗡鸣"
- ltx 家族：英文自然语言完整句，描述 camera movement（dolly in / pan left / static shot）
- 帧数换算：24fps → 124帧≈5.2秒（minimax 训练区间 124-362）；121帧≈5秒（ltx）

## 成本警告（必做）
执行前告知用户："视频生成约需 X-Y 分钟，期间 GPU 满载"。用户确认后再跑。

## 时长参考（本机 12GB）
- minimax turbo 8步 768x448 124帧：约 5-15 分钟
- ltx 10步 768x512 121帧：约 10-30 分钟

## 评估
视频评估由引擎自动执行（runner 检测视频产物强制抽帧评估），也可主动 view_video(use_last=true) 复评。

## 多段续接模式（10秒及以上长视频）

**为什么拆段**：12GB 显存下单段 124 帧（≈5s）已接近满载；更长视频必须分段生成后续接。

**拆分方法**：N 秒视频 = ⌈N/5⌉ 段，每段 124 帧（24fps）。例：10s = 段1(t2v 124帧) + 段2(i2v 124帧)。

**续接方法（核心）**：段 N+1 的首帧 = 段 N 的末帧，保证画面连贯：
1. 段1 用 minimax_t2v（纯文字生成）
2. 段2 改用 minimax_i2v，image 参数填段1 的末帧图片

**取末帧（模板优先！）**：直接用模板 `run_template(extract_frame)`（参数 video=视频文件名、frame_index=帧数-1；124帧视频末帧=123），不要现场搭节点链。视频文件先用 upload_image 送入 /input。

**合并（模板优先！）**：直接用模板 `run_template(merge_videos)`（参数 video1/video2=两段视频文件名）。不要用 synthesize 现场搭合并工作流——本机蓝图《Merge Videos》已模板化。

**节点链参考**（模板背后的原理，仅当模板不够用才自己搭）：
取帧 = LoadVideo → GetVideoComponents → ImageFromBatch(batch_index, length=1) → SaveImage
合并 = LoadVideo×2 → GetVideoComponents×2 → BatchImagesNode(images=[两路帧]) → CreateVideo(fps=24) → SaveVideo

**交付形态**：合并后的完整视频为主产物，各分段视频一并列出供用户选用。

**显存纪律**：每段执行完由引擎自动卸载；两段串行执行，绝不并行。
