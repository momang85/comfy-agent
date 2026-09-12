# 端到端实机测试报告 · 轮次 2（2026-09-11）

同一套任务序列、**严格串行**（每步等 stage 回 idle 才发下一步），与轮次 1 逐项对比。
监控：服务端事件流（SSE）+ `history.jsonl` + `nvidia-smi`；本轮全程启用新的温度护栏与进度心跳。

## 一、结果总览

| 步 | 任务 | 轮次 2 | 轮次 1 | 关键证据 |
|---|---|---|---|---|
| A1 | 512 文生图 | ✅ **一次通过** | ⚠️ 失败 3 次后成功 | 单条 `run_template ok`；警告含模型名归一 |
| A2 | 遮罩局部重绘 | ✅ **一次通过** | ⚠️ 失败 2 次后成功 | analyze_image → run_template ok（评估 9/10） |
| A3 | 风格转绘 | ✅ **一次通过** | ❌ 失败无产物 | `style_transfer` completed，评估 9/10 |
| A4 | 放大两倍 | ✅ **一次通过** | ❌ 语义漂移（改成重画） | `upscale_pass` → `agent_upscale_00011_.png` |
| B1 | 5 秒视频 | ✅ 成功（**3 次尝试**） | ❌ 使 ComfyUI 崩溃 | `agent_minimax_00010_.mp4`；采样期峰值 84°C |
| B2 | 取末帧 | ✅ **一次通过** | ❌ 服务器 400 + 语义漂移 | 警告：`video 已自动上传到 /input` |
| B3 | 末帧续接 5 秒 | ✅ **一次通过** | ⚠️ 87°C 人工中断 | `agent_minimax_00011_.mp4`，评估 8/10 |
| B4 | 合并 10 秒 | ✅ **一次通过** | ✅（引擎路径） | `video1/video2 已自动上传`，视频评估 9/10 |
| B5 | 续到 15 秒 | ⛔ **未执行**（预算耗尽，如实标注） | ⛔ 未执行 | — |
| B6 | 插帧/高清化问询 | ✅ **诚实降级** | ✅ 同 | 明确答"未装 Frame Interpolation、无视频超分链路"，未编造节点、未违规生成 |

**结论**：轮次 1 暴露的 P0/P1 问题在轮次 2 全部消失——项目 A 四项首次全部一次通过；
项目 B 除"续到 15 秒"未跑外，取帧/续接/合并/降级问询四条链路一次通过。

## 二、本轮修复的实机验证

| 修复 | 验证证据 |
|---|---|
| P0-1 模型名归一 | 每步警告都记录 `'sdXL/novaAnimeXL_ilV180.safetensors' → 'sdXL\novaAnimeXL_ilV180.safetensors'`，且再无 `value_not_in_list` |
| P1-1 选项回落模板推荐值 | （本轮大脑未再传非法采样器值；上轮验收已验证 `dpmpp_2m_karras → dpmpp_2m`） |
| P0-4 引擎代传输入文件 | B2/B3/B4 警告显示 `video/video1/video2/image 已自动上传到 ComfyUI /input` —— 上轮这些位置全是服务器 400 |
| P1-2 评估仲裁 | 每步只出现 1 次评估事件、1 张评估卡；无"引擎 9 分 / 大脑 6 分"冲突 |
| P1-4 use_last 回退 | B2 在"上一轮产物引用为空"的情况下仍取到正确视频 |
| **P2-1 进度心跳** | SSE 实测：`{"event":"progress","data":{"prompt_id":"7060025e","queue_position":1,"elapsed_sec":46}}` → 前端显示"队列 1 · 已运行 46秒" |
| **P2-2 警告独立区域** | 警告不再顶掉阶段显示（警告经 `#warnline`，12 秒自动消失） |

## 三、本轮新发现（1 项，已当场修）

**【高 · 自伤】温度熔断默认 85°C 会误杀视频渲染**
- 实测：本机视频渲染常态 **80-86°C**，峰值 **86°C**（B1 第一次尝试）、**89°C**（第二次尝试）——两次都被我新上线的
  85°C 熔断自动中断，返回 `stage: execution_failed` 且无产物（B1 因此耗了 3 次尝试）
- 根因：`comfy_agent/guard.py` 单级阈值，未考虑本机（RTX 5070 Ti Laptop 12GB）视频采样的真实温度区间
- 修复（已改）：改为**两级护栏** —— `GPU_TEMP_WARN=88`（提醒，2 分钟一次）/
  `GPU_TEMP_LIMIT=92`（熔断中断）；改后在 84°C 峰值下 B1 正常完成
- 遗留：阈值仍是"一刀切"，未按任务类型区分；建议后续把视频任务单独放宽（例如 93）并在前端温度超 88 时显著提示

## 四、仍未解决 / 未覆盖（如实）

- **B5（续第三段 → 15 秒）未执行**：预算耗尽。已具备的前置条件都验证过（i2v 续接 B3 成功、合并 B4 成功），
  剩余风险点是 `run_count>4` 上限与单次 30 分钟等待超时
- **A2 本轮实际走的模板未逐一确认**（事件窗口只显示 `run_template`，未打印 template_id）；产物与评估正常
- **音频**仍未实现（`VAEDecodeAudio` + `CreateVideo.audio` 存在但未验证接线，需真机长视频验证）
- **LTX 模板（ltx_i2v）本轮与上轮都未实测**；LTX 的 8k+1 帧对齐也未实现（仅上界护栏）
- **温度护栏与任务类型无关**：图像任务（50-60°C）也被同一阈值约束，无实质影响但不够精细

## 五、硬件安全记录

| 阶段 | 温度 | 说明 |
|---|---|---|
| 图像任务全程 | 51-56°C | 低分辨率串行，无压力 |
| B1 视频采样 | 峰值 84°C（前两次 86/89°C 触发旧阈值） | 两级护栏后未再中断 |
| B3 视频采样 | 峰值 88°C | 触发**提醒**（未熔断），符合设计 |
| 任务间空闲 | 57-63°C | 模型自动卸载 |

全程未出现 ≥92°C；无人工中断（本轮），全部由护栏按设计处理。

---

## 六、遗留测试补跑（同日追加）

### B5 续到 15 秒：链路跑通，但**产物时长不符**

- 实际动作：取末帧（`agent_frame_00005_.png`，引擎自动上传 ✓）→ `minimax_i2v` 第三段（`agent_minimax_00012_.mp4`，5.17 秒 ✓）→ `merge_videos`
- **结果偏差**：请求是"把这三段合成约 15 秒"，产物 `agent_merged_00005_.mp4` 实测 **10.33 秒 / 248 帧**（= 5.17 + 5.17）
- 证据（事件原文）：`START merge_videos {"video1": "agent_minimax_00011_.mp4", "video2": "agent_minimax_00012_.mp4"}`
  —— 大脑合的是**两段单段视频**，而不是"已有的 10.33 秒合成片 + 新的第三段"（那才是 15.5 秒）
- 判断：**数量/集成偏差**（与轮次 1 的"放大→重画"同族）。根因是链条缺少"目标时长核对"：
  引擎评估视频时只抽帧看画质，不回报时长；大脑因此无法自查"够不够 15 秒"
- 建议修法：`runner` 在视频产物落盘后用 ffprobe 回报 `duration_sec`（ffmpeg 已在用），
  大脑交付前比对该值与用户要求；或 `merge_videos` 增加 `target_seconds` 参数，不足时提示还需要接哪一段

### LTX 模板（ltx_i2v）实测：**因环境不可达未能执行**

- 消息已接收，但大脑一个工具都没调用就结束了；`history.jsonl` 记录：
  `ERROR: LLMError: 无法连接 LLM 服务（https://api.z.ai/api/paas/v4）: [WinError 10061] 由于目标计算机积极拒绝，无法连接`
- 根因：**代理关闭后国际站 api.z.ai 不可达**（B5 期间还通，随后断网/断代理）。属环境问题，非代码缺陷
- 顺带确认：新的错误处理路径**生效**——发出 `ERROR` 事件并复位到 `idle`，前端会显示错误卡，不再静默卡在"构建中"
- **模型齐备性已核验**（LTX 可跑的前提都在）：`checkpoints/ltx-2.3-22b-distilled-1.1.safetensors` ✓、
  `loras/ltx-2.3-22b-distilled-lora-384-1.1.safetensors` ✓、`text_encoders/gemma-3-12b-it-...` 两分片 ✓
- **待查的设计风险**（供下次实测重点验证）：`comfy_agent/templates/video.py` 的 LTX 链用
  `CheckpointLoaderSimple(LTX_CKPT)` 的 **槽 1 取 CLIP**，而 LTX-2.x 的文本编码器是 text_encoders 里**独立的 Gemma-3 分片**——
  模板没有加载它。该链路很可能在服务器端直接失败（clip 类型/权重不匹配），需在 LLM 可用时实测定性

### LTX 修复与实测（第二轮追加：设计风险已定性与修复）

**修复前实测（实证定性）**：`ltx_i2v` 提交后 `execution_failed`，节点 4 报
`ERROR: clip input is invalid: None — If the clip is from a checkpoint loader node your checkpoint does not contain a valid clip or text encoder model`
→ 证实"用 LTX checkpoint 槽 1 当 CLIP"是**真 bug**（不是猜测）。

**修复内容**（`comfy_agent/templates/video.py`，签名均按本机 object_info 核对）：
1. 文本编码改走专用加载器：`LTXAVTextEncoderLoader(text_encoder=<Gemma 分片>, ckpt_name=LTX_CKPT)` → 正/负两个 `CLIPTextEncode` → `LTXVConditioning(frame_rate=24)` → `LTXVImgToVideo`（修掉了"正负接同一路"的退化 CFG）
2. **音频链**：`LTXVAudioVAELoader` + `LTXVEmptyLatentAudio` + `LTXVConcatAVLatent` → 采样 → `LTXVSeparateAVLatent` → `VAEDecode`(视频)/`LTXVAudioVAEDecode`(音频) → `CreateVideo(images, audio, fps)` → `SaveVideo`
3. 帧数 **8k+1 对齐**（`_LengthUnitsMixin.GRID=(8,1)`）：`duration=4`→96 帧会被对齐为 97（96 对 `LTXVImgToVideo` 是非法值）
4. `models_used` 补 Gemma 分片；新增 `text_encoder` 参数；引擎新增**通用模型参数归一** `_canonical_model_params`（把 `text_encoder` 等非 ckpt 的模型名归一为清单形态——Gemma 名在 Windows 清单里是反斜杠）

**修复后实测**：
- ✅ 校验通过（原来的 `clip input is invalid` 消失）；`Value not in list` 也消失（归一生效）
- ❌ 执行期新报 **`invalid tokenizer`**：已核实该 Gemma 目录**tokenizer 文件齐全**（`tokenizer.json/.model/tokenizer_config.json/special_tokens_map.json`）→ 说明本机这版 `LTXAVTextEncoderLoader` **不认分片式 Gemma 目录**（官方 LTX-2.3 工作流用的是单文件 Gemma）
- 另注：该 LTX checkpoint 达 **43GB**（22B bf16）+ Gemma 7.4GB，本机 12GB 显存 / 32GB 内存，即使装上单文件 Gemma 也是"能跑但极慢"的量级

**结论（定性）**：LTX 链路的**代码缺陷已修**（接线正确、校验通过、音频链与网格对齐就位）；
剩余阻塞是**模型格式/加载器兼容**问题，需要装 LTX 推荐的单文件 Gemma 文本编码器——
这正是本轮新增"缺模型搜索下载"功能的用武之地。

附带修掉一个报告类 bug：`run_workflow` 在 `execution_failed` 时只改 `stage` 不回写 `ok`，
导致失败结果 `ok: True`（调用方会把失败当成功）——已在两处失败分支显式置 `ok: False`。


### 本轮补跑的另外两点观察

- 自动上传（P0-4）在每一步都生效：`image/video/video1/video2 已自动上传到 ComfyUI /input`
- 视频评估出现 `verdict=False, score=8`（画质分尚可但被判不通过）：引擎用的是**通用 criteria**
  （"视频帧质量良好、动作连贯无明显畸形"），对"橘猫是否真的走了两步"这类**任务语义**天然判不准——
  这也是上方 B5 数量偏差未被拦下的原因之一

