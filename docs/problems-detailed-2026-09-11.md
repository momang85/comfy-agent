# comfy-agent 缺陷登记报告（详细版 · 2026-09-11）

来源：本轮实机端到端测试（启动脚本拉起服务 → 前端输入任务 → 实时监控 → 与 history.jsonl 交叉验证）。
每条含：**现象 / 证据 / 代码根因 / 影响 / 修复方案 / 验证方式**。

严重度：**P0=阻断或设备风险** · **P1=高（结果错误/资源浪费）** · **P2=中（体验/运维）** · **P3=轻**

---

## P0-1 模型名路径分隔符回归（阻断）

- **现象**：所有图像任务在服务器端被拒 `value_not_in_list`；同一模型、同一模板，只差分隔符就一败一成。
  t2i 失败 3 次后成功；inpaint/i2i 各失败 2 次；**style_transfer 与 upscale_pass 完全无法成功**。
- **证据**：`proj_8a4f0ee7/history.jsonl` 调用序列——
  ```
  t2i            ckpt='sdXL/novaAnimeXL_ilV180.safetensors'  → 400 Value not in list
  style_transfer ckpt='sdXL/novaAnimeXL_ilV180.safetensors'  → 400 Value not in list
  i2i            ckpt='sdXL/novaAnimeXL_ilV180.safetensors'  → 400 Value not in list
  i2i            ckpt='sdXL\novaAnimeXL_ilV180.safetensors'  → ✅ completed
  ```
  服务器报错 `{"kind":"server_node","node":"5","message":"Value not in list","type":"value_not_in_list"}`
  （node 5 = CheckpointLoaderSimple）。
- **代码根因（四处串联）**：
  1. `comfy_agent/templates/image.py:11-12` 默认值改为 POSIX 分隔符（上一轮可移植性改造引入）
  2. `comfy_agent/model_adapt.py:60-62` `checkpoint_exists()` 用 `find_model()`——分隔符不敏感的模糊匹配
     （`knowledge.py:211-231` 归一化）→ 认定"存在"→ 不改写参数
  3. `comfy_agent/validate.py:139-145` 文件枚举比较同样归一化 → **本地校验通过**（掩盖不一致）
  4. 服务器按精确清单校验 → 400
  5. `comfy_agent/validate.py:155-156` 建议值取自 `choices_norm`（`/` 形态）→ `repair.py:109-112` 写回 →
     **再次被拒**，形成"修了还是错"的循环
- **影响**：每任务浪费 2-4 次执行 + 2-4 轮 LLM 往返；两个已交付模板不可用。
- **修复**：新增 `Knowledge.resolve_model_name()`（归一化比对后**返回清单原始字符串**）；
  `model_adapt` 把 canonical 名写回 params；`validate` 建议值用**原始** `choices[idx]`，
  并对分隔符不一致额外产出 warning（可见不静默）。
- **验证**：项目 A 四项各一次通过；单测覆盖"`/` 形式在本机 `\` 清单下解析为 `\`"。

---

## P0-2 视频任务导致 ComfyUI 硬崩溃（阻断 · 设备风险）

- **现象**：视频任务执行中 ComfyUI 进程直接死亡，引擎侧只见 `ConnectionResetError`。
- **证据**：日志 `Windows fatal exception: access violation`，崩溃前最后为 `model_type FLOW_AV`；
  **反证**：同任务同参数，仅去掉 `--highvram` → 成功，日志 `Model MiniMaxH3 ... 19995MB Staged`，8 步采样正常。
- **代码根因**：
  1. `scripts/一键启动.bat:93` 启动 ComfyUI 带 `--highvram`
  2. 本机 `ComfyUI/comfy/cli_args.py:169` 该参数=模型常驻显存；`:315` 明确它会**关闭 DynamicVRAM**
  3. H3 INT8 权重实测 **19995MB** > 12GB → 常驻模式崩溃
  4. 附带：`--normalvram` 仍在 usage 文本但 `cli_args.py:169-171` 已移除 → 照 usage 写脚本会 argparse 报错
- **影响**：视频链路完全不可用；进程崩溃丢失 history 条目（引擎只能报"连不上"）。
- **修复**：`一键启动.bat` 默认不加 `--highvram`；另立 `一键启动_高显存.bat`（仅图像）；README 注明。
- **验证**：启动脚本起服务后跑 5 秒视频不再崩溃（本轮已用默认显存实证）。

---

## P0-3 视频 OOM 无自动降参（高）

- **现象**：视频 OOM 时引擎只给建议，不自动降参。
- **代码根因**：`comfy_agent/repair.py:212-233` 只处理 `EmptyLatentImage/EmptySD3LatentImage/EmptyLatent`
  的 width/height 与 batch_size；视频模板用 `MiniMaxH3ImageToVideo` / `EmptyMiniMaxH3LatentAV` / `LTXVImgToVideo`
  ——一个都不匹配。
- **修复**：扩展为按节点类枚举数值输入：视频节点降 width/height（×0.6）与 length（×0.5）并联动提示。
- **验证**：单测构造视频 OOM 错误 → 断言给出 applied 建议。

---

## P0-4 输入"上传"没有强制（高）

- **现象**：B2「取最后一帧」失败（`extract_frame` 服务器 400），随后大脑语义漂移去重生成视频；
  同任务走引擎 CLI（先 upload 再 extract）**成功**。
- **代码根因**：`templates/video.py:290-325`、`:243-287` 要求文件已在 ComfyUI `/input`；
  `brain/skills/video.md:39` 虽写明"先用 upload_image 送 /input"，但**引擎不校验也不代传**，全靠大脑记得。
- **修复**：模板声明输入文件依赖；`runner.run_template` 对"值是本机已存在路径"的参数**自动上传**并替换为 server 名，
  结果记入 warnings。
- **验证**：B2/B4 由大脑自由表述一次通过；单测覆盖"本地路径→自动上传→参数替换"。

---

## P0-5 渲染期无温度保护（设备风险）

- **现象**：视频渲染常态 82-84°C；i2v 续接冲到 **87°C**，靠人工 `/api/interrupt` 停下。
- **证据**：`nvidia-smi` 采样——空闲 53°C；渲染 82-84°C/100%/8-10GB；中断后 12-15 秒降到 66-68°C、显存回落 1.1GB。
- **代码根因**：`scripts/run_complex_task.py:81-141` 已有温度轮询+熔断，但**只存在于测试脚本**；
  `brain/web/server.py` 监控线程只报显存与队列。
- **修复**：服务侧加温度轮询（`GPU_TEMP_LIMIT` 默认 85°C）→ 超限自动 interrupt + `error` 事件；
  `vram` 事件带 `temp_c`，前端顶栏显示。
- **验证**：单测注入假温度触发熔断；实机渲染时前端可见温度。

---

## P1-1 枚举修复回落"字符串最接近"（高）

- **现象**：`sampler_name='dpmpp_2m_karras'`（采样器+调度器并成一个非法值）→ 被改成 `dpmpp_2m_sde`
  （祖先采样器，特性不同），而非模板默认 `dpmpp_2m`。
- **代码根因**：`validate.py:227-234` `_closest()` 取 `SequenceMatcher` 最高分；
  `repair.py:109-112` 无条件采用，**不知道模板推荐值**。
- **修复**：`Template.normalize_params()` 做 choice 收敛（recommended → default → choices[0]）并出 note；
  相似度匹配仅用于文件名类输入。
- **验证**：单测断言 `dpmpp_2m_karras → dpmpp_2m`。

---

## P1-2 同一产物两份评估、结论相反（高 · 资源）

- **现象**：引擎强制评估三段视频均 **9/10 通过**，大脑 `view_video` 判 **6/4/5 不通过** →
  连续重生成 3 段（≈18 分钟满载），撞上限后交付"视频是静态图、失败"。
- **代码根因**：`runner.py:164-165,212-244` 用**通用 criteria** 强制评估（抽样 4 帧）；
  `brain/tools.py:359-397` 的 `view_video` 用**任务 criteria**；两处都发 `evaluation` 事件但无 `prompt_id` 去重，
  大脑上下文也没有"引擎已评估且通过"的信息。
- **修复**：`runner` 把评估结论（`prompt_id`/verdict/score）写入 `ctx`；大脑 `view_image/view_video` 命中同 `prompt_id`
  且已通过 → 直接复用引擎结论，不再调 VLM；前端评估卡按 `prompt_id` 去重。
- **验证**：一段视频只生成 1 次、只出现 1 张评估卡。

---

## P1-3 崩溃后无自愈 + 死引用（高）

- **现象**：ComfyUI 崩溃后大脑只能转述"请先通过绘世启动器启动"；`repair.py` 建议的 `run_lowvram.bat` **不存在**。
- **代码根因**：`client.py:44-53` 只报连接失败文案；`repair.py:236-237` 指向不存在脚本；服务侧无进程级健康检查。
- **修复**：文案改**可执行**指引（"运行 `scripts\一键启动.bat`，它会检测并拉起"）；清死引用；
  可选 `AUTO_START_COMFY=1` 时由引擎自动拉起。

---

## P1-4 `view_video(use_last)` 脆弱（中）

- **现象**：报"上一轮产物中没有视频"，尽管项目里已有 3 个 mp4。
- **代码根因**：`brain/tools.py:359-397` 只看**最近一次运行**的产物，中间失败即覆盖引用。
- **修复**：改为"项目内最近一次**视频**产物"（按 mtime 扫项目 outputs）。

---

## P1-5 "音画同生"名不符实（中）

- **现象**：提示词写了"背景有鸟鸣声"，产物 mp4 **无音频流**（ffprobe 核验）。
- **代码根因**：`templates/video.py:138-143` 链条末端 `VAEDecode`（仅视频）→ `CreateVideo(images)` → `SaveVideo`，
  **无音频解码/合流节点**，`EmptyMiniMaxH3LatentAV` 的音频潜变量被丢弃。
- **修复**：查本机是否有 H3 音频解码节点，有则接入；无则改掉"音画同生"表述并在 README 说明仅视频轨。

---

## P1-6 任务语义漂移（高 · 体验）

- **现象**：A4「放大两倍」失败后**改为重新生成**新图；B2「取最后一帧」失败后**改为重生成视频**。
- **代码根因**：系统提示的失败处理只有"做语义级修复"，缺顺序约束；`brain/agent.py:92-109` 的重复护栏只换种子。
- **修复**：系统提示明确顺序：**① 修参数（同名模板重试且必须改具体参数）→ ② 换等价工具 → ③ 降级需求并明确告知**；
  禁止把"局部操作"改成"整体重新生成"；交付时若未满足原请求必须标注。

---

## P2 体验与运维

| ID | 现象 | 代码根因 | 修复 |
|---|---|---|---|
| P2-1 | 渲染期零进度（5-15 分钟只有"阶段: running"） | `runner.py:122` 只发一次 running；进度靠 `server.py:141-152` 的显存/队列轮询，单任务 `queue_position` 恒为 1 | 轮询 ComfyUI 执行进度转 `progress` 事件 |
| P2-2 | 警告覆盖状态行 | `app.js:604-609` warning 直接写 `#statusline` 且 `break` | 警告走独立区域，不覆盖阶段 |
| P2-3 | 重复评估卡 | 同 P1-2 | 前端按 `prompt_id` 去重 |
| P2-4 | 启动脚本：缺路径回落 / PID 不校验 / 日志截断 / 路径未规范化 | `一键启动.bat:23-28,46-53,93,116`、`config.py:93` | 回落探测、kill 前校验、日志追加、`resolve()` |

---

## 附：本轮"通过项"（修复时不得回归）

| 项 | 证据 |
|---|---|
| 多项目隔离 | 切项目后 15 秒窗口 444 条事件全属当前项目，跨项目 0 条 |
| 遮罩附件管线 | DataTransfer 注入 → 上传 → 气泡缩略图 → 大脑用作 inpaint 的 mask |
| 局部重绘链路 | analyze_image(底图) → upload_image → inpaint(image+mask) → 出图 → 评估 |
| 参数护栏 | `cfg=7.5 → 3.0` 收敛告警；重复运行 `seed 12345 → 0` |
| 提示词体检 | "缺 lighting"、512 分辨率告警均在卡片/状态行出现 |
| 启动脚本自愈 | ComfyUI 崩溃后重跑 launcher 自动拉起，Web UI 正确复用 |
| 降级诚实性 | B6 明确回答"未装 Frame Interpolation/无视频超分"，未编造节点，并遵守"不要生成" |
| 中断链路 | `/api/interrupt` 12-15 秒内满载→安全温度并卸载模型 |
