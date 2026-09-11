# 端到端实机测试报告（2026-09-11）

测试方式：用 `scripts/一键启动.bat` 拉起服务 → 内置浏览器从前端输入任务 → 实时监控前端渲染 →
结束时与 `.comfy-agent/projects/<id>/history.jsonl` 交叉验证。全程只走前端/API，不改代码。

监控手段：
- 页面注入**独立 SSE 记录器**（第二个 EventSource 订阅 `/events`，带时间戳落 `window.__ev[]`），
  与应用渲染解耦，作为客观事件流证据
- 每 15-100 秒轮询 DOM（状态行/阶段条、工具卡、气泡、工作流图、产物画廊、评估卡、错误卡）
- 结束用 `history.jsonl` 交叉验证（权威事件记录）

---

## 一、环境与启动

| 项 | 结果 |
|---|---|
| `一键启动.bat`（coexist） | ⚠️ 首次直接失败：`[ERROR] ComfyUI path not configured` |
| 走 `install.bat`（喂入路径） | ✅ 正常：校验 `python\python.exe` 与 `ComfyUI\main.py` 后写 `comfy_root.local` |
| 二次启动 | ✅ ComfyUI PID 3340 / Web UI PID 3316，`webui.pid` 正确写入，浏览器自动打开 |
| 崩溃后再次启动 | ✅ 检测到 ComfyUI 已死→自动拉起；Web UI 正确 `[Reuse]` |
| 过期 PID 隐患 | ⚠️ `.comfy-agent/webui.pid` 里是已不存在的 8844；`/restart` 会无条件按该 PID 杀进程（PID 复用时会误杀） |

**问题 E1（中）启动脚本硬依赖 `comfy_root.local`**：`一键启动.bat:23-28` 只认 环境变量 →
`comfy_root.local`，否则报错退出；不回落 `comfy_agent/config.py:12` 的平台默认值，也不探测常见安装位置。
**问题 E2（中）过期 PID 文件**：`brain/web/server.py:498-502` 写 PID、`:519` 在正常退出时删除；
被强杀/崩溃时残留。`一键启动.bat:46-53` 不校验 PID 是否仍是 Web UI 就直接 `taskkill`。
**问题 E3（轻）日志覆盖**：`一键启动.bat:93,116` 用 `>`（截断）重定向，重启即丢崩溃现场——
本次 ComfyUI 崩溃日志只在重启前可取到。

---

## 二、项目 A「图片改图链路」（新项目 `proj_8a4f0ee7`）

| # | 输入 | 结果 | 判定 |
|---|---|---|---|
| A1 | 画一只戴红色围巾的小狐狸，1024x1024 | 失败 3 次后成功出图（`agent_t2i_00052_.png`），评估 9/10 | ⚠️ 有可用结果但代价高 |
| A2 | 遮罩图附件 + 把刚才那张图按这张遮罩做局部重绘：遮罩区域改成一顶红色小帽子 | 失败 2 次后成功（`agent_inpaint_00001_.png`），9 节点 12 连线，评估 9/10 | ⚠️ 同上 |
| A3 | 把这张图改成吉卜力动画风格 | **失败，无产物**（大脑最后输出一段"操作建议"） | ❌ 不符预期 |
| A4 | 把这张图放大两倍 | **失败，且语义漂移**：upscale 失败后大脑改为**重新生成**了一张新图 | ❌ 不符预期 |

**关键发现 P0-1（阻断）模型名路径分隔符回归 —— 本次测试的头号问题**

现象：所有图像任务在**服务器端**被拒 `value_not_in_list`；同一模型、同一模板，只差分隔符就一败一成。

证据（`proj_8a4f0ee7/history.jsonl` 的 `run_template` 调用序列）：
```
t2i              ckpt='sdXL/novaAnimeXL_ilV180.safetensors'   → 400 Value not in list
style_transfer   ckpt='sdXL/novaAnimeXL_ilV180.safetensors'   → 400 Value not in list
i2i              ckpt='sdXL/novaAnimeXL_ilV180.safetensors'   → 400 Value not in list
i2i              ckpt='sdXL\novaAnimeXL_ilV180.safetensors'   → ✅ completed
```
服务器报错：`{"kind":"server_node","node":"5","message":"Value not in list","type":"value_not_in_list"}`（node 5 = CheckpointLoaderSimple）。

代码级根因（四处串联）：
1. `comfy_agent/templates/image.py:11-12` —— 模板默认模型名在本轮可移植性改造中改成了 POSIX 分隔符
   （`sdXL/novaAnimeXL_ilV180.safetensors`）
2. `comfy_agent/model_adapt.py:60-62` —— `checkpoint_exists()` 用 `find_model()` 模糊匹配，
   而 `find_model()` 会把 `\` 与 `/` 归一（`knowledge.py:211-231`）→ **认为"存在"，于是不改写**
3. `comfy_agent/validate.py:139-145` —— 文件枚举比较同样做了分隔符归一 → **本地校验通过**，把不一致掩盖掉
4. 服务器按自己的精确清单校验（Windows 下是 `sdXL\novaAnimeXL...`）→ 400
5. `comfy_agent/validate.py:155-156` —— 一旦判为未知值，建议值取自 `choices_norm`（已归一为 `/`）
   → `repair.py:109-112` 把 `/` 形式写回 → **再次被服务器拒绝**，形成"修了还是错"的循环

影响面：t2i 多花 3 次执行、inpaint/i2i 各多花 2 次；**style_transfer 与 upscale_pass 完全无法成功**（其默认 ckpt 即 `/` 形式），
并间接导致 A4 的语义漂移。每条失败还各消耗一轮 LLM 往返。

修复建议（代码级）：
- `model_adapt` 不只判"是否存在"，还要**解析出本机清单中的精确条目并写回参数**
  （如 `checkpoint_exists()` → 返回 canonical 名；`adapt_ckpt()` 用它覆盖 `params["ckpt"]`）
- `validate.py` 的建议值改用**原始 choice 字符串**（`choices[idx]`），绝不用归一化副本
- 分隔符不一致应产出 warning（可见），而不是静默判过

**问题 P1-1（高）枚举修复回落"字符串最接近"而非模板推荐值**
症状：大脑传 `sampler_name='dpmpp_2m_karras'`（把采样器与调度器并成一个非法值）→ 确定性修复改成
`dpmpp_2m_sde`（祖先采样器，输出特性变了），而非模板默认的 `dpmpp_2m`。
根因：`comfy_agent/validate.py:227-234` `_closest()` 取 `SequenceMatcher` 最高分；
`comfy_agent/repair.py:109-112` 无条件采用该建议，不知道模板/参数 schema 的 default。
建议：枚举修复优先回落"模板 default / Param.choices 的首选"，相似度匹配仅作次选。

**行为类发现**
- **B1（中）任务语义漂移**：A4 请求"放大两倍"，upscale 失败后大脑改为重新生成一张图并交付——
  用户拿到的不是他要的东西。系统提示里"失败后做语义级修复"缺少"先修参数、再换方案、最后才降级需求"的顺序约束。
- **B2（中）自愈能力不稳定**：A1 通过 `list_models` 拿到正确名字后自愈成功；A3 同样拿到正确名字却放弃、
  转而输出"操作建议"给用户；A3' 又改走 i2i。同一根因，三种不同结局。
- **B3（轻）交付文本含幻觉细节**：交付语称"保留了银发红瞳特征"（原图并不具备）。

**通过项（正面结论）**
- A2 的**遮罩附件管线完整可用**：经 `DataTransfer` 注入文件 → 应用自带上传/缩略图 → 大脑
  `analyze_image`(底图) → `upload_image`(底图入 /input) → `run_template(inpaint, image+mask)` → 出图 → 评估
- 局部重绘工作流 9 节点 12 连线，`VAEEncodeForInpaint` 与 tiled 解码均正确执行
- 本轮新加的两个护栏**实测生效**：`cfg=7.5 → 3.0` 收敛告警（连图卡可见）、
  重复运行自动换随机种子（`seed 12345 → 0`）
- 提示词维度体检（"缺 lighting"）与分辨率提醒（512 低于原生区间）均在工具卡/状态行出现

---

## 三、项目 B「视频续接链路」（新项目 `proj_661c0b1f`）

| # | 输入 | 结果 |
|---|---|---|
| B1a | 生成一段5秒的视频（默认启动脚本 --highvram） | ❌ **ComfyUI 直接崩溃**（进程死亡），引擎只看到 ConnectionResetError |
| B1b | 换默认动态显存后同参数重试 | ✅ **成功**：`agent_minimax_00006_.mp4`，引擎强制抽帧评估 **9/10 通过**；ffprobe 核验 768x448 / 24fps / **124 帧 / 5.17 秒** |
| B1c | 大脑二次评估冲突 | ❌ 大脑 `view_video` 判 6/4/5 分 → **连续重生成 3 段**（00006/00007/00008，≈18 分钟 GPU），撞执行上限后向用户交付"视频是静态图、失败" |
| B2 | 把最后那段视频的最后一帧取出来 | ❌ 大脑失败：`extract_frame` 服务器 400（**未先把视频上传到 /input**）；随后语义漂移又开始重生成视频（00009） |
| B2' | 同任务走引擎 CLI（先 upload 再 extract_frame） | ✅ **成功**：`agent_frame_00002_.png` → 证明模板无问题，失败根因是大脑漏步骤 |
| B3 | 用末帧 i2v 续接 5 秒 | ⚠️ **因温度 87°C 被人工中断**（渲染到 1/8 步），未取得结果 |
| B4 | 合并两段成 10 秒（引擎 CLI） | ✅ **成功**：`agent_merged_00002_.mp4`，ffprobe 核验 **248 帧 / 10.33 秒**（5.17+5.17），引擎评估通过 |
| B5 | 续第三段并合并到 15 秒 | ⛔ 未执行（B3 中断 + 硬件保护） |
| B6 | 能否插帧 48fps + 高清化（明确要求不要生成新视频） | ✅ **通过**：大脑引用能力表如实回答"本机未装 Frame Interpolation、无视频超分链路"，给出安装建议，**未编造节点/模板**，也未违规生成 |

### 新增发现（B 段）

- **P1-6（高）取帧/合并类任务的前置条件没人强制**：`comfy_agent/templates/video.py:290-325`（extract_frame）与
  `:243-287`（merge_videos）要求文件已在 ComfyUI `/input`，`brain/skills/video.md:39` 也写了"先用
  upload_image 送入 /input"——但**引擎不校验、不代传**，全靠大脑记得。实测大脑直接漏掉，
  服务器以 `LoadVideo.file` 的 `value_not_in_list` 拒绝。建议：模板声明输入文件依赖，引擎自动上传。
- **P1-7（中）`view_video(use_last=true)` 脆弱**：失败一次中间调用后即报"上一轮产物中没有视频（先跑视频模板）"，
  尽管项目里已有 3 个 mp4。根因：`brain/tools.py:359-397` 的 `use_last` 只看**最近一次运行的产物**，
  中间失败会把引用覆盖掉。建议：改为"该项目内最近一次视频产物"。
- **P2-4（中）大脑把"取帧"执行成"重新生成视频"**（B2 实测），与 A4 的"放大→重生成"同源：
  系统提示缺少"先修参数 → 再换工具 → 最后才降级需求"的顺序约束。

### 硬件安全记录（本轮实测）

| 时点 | 温度 | 说明 |
|---|---|---|
| 空闲 | 53°C | 基线 |
| 视频渲染中 | **82-84°C / 100% / 8-10GB** | 单段 5 秒视频（8 步）的常态 |
| 中断后 12-15 秒 | 66-68°C / 0% / 1.1GB | 模型自动卸载，降温很快 |
| i2v 续接（B3） | **87°C** | 触达人工安全线，立即 `/api/interrupt` 中止 |
| 中止后 35 秒 | 63°C | 恢复安全 |

**结论**：`POST /api/interrupt` 的实际效果良好（12-15 秒内从满载降到安全温度并卸载模型）；
但**渲染期间没有任何自动温度保护**——本次靠人工盯守。建议把 `scripts/run_complex_task.py` 里已有的
`nvidia-smi` 温度轮询做成引擎默认能力（≥85°C 自动中断）。

### P0-2（阻断）视频模板会让 ComfyUI 硬崩溃 —— 根因是启动脚本的显存参数

现象：`comfyui.log` 出现 `Windows fatal exception: access violation`；崩溃前最后正常日志为
`model_type FLOW_AV`（MiniMax H3 装载阶段）。引擎侧表现为 `ConnectionResetError`（无 OOM 语义）。

代码级根因：
1. `scripts/一键启动.bat:93` 启动 ComfyUI 时带 `--highvram`
2. 本机 ComfyUI 源码 `ComfyUI/comfy/cli_args.py:169` 定义该参数为"模型常驻显存"，
   `:315` 明确 `--highvram` 会**关闭 DynamicVRAM**（`return not args.disable_dynamic_vram and not args.highvram ...`）
3. 而 MiniMax H3 的 INT8 权重实测 **19995MB**（日志原文：`Model MiniMaxH3 prepared for dynamic VRAM loading. 19995MB Staged`），
   远超本机 12GB 显存 → 常驻模式下崩溃
4. 附带发现：`--normalvram` 出现在该版本的 usage 文本里，但 `cli_args.py:169-171` 的 vram 组**只有**
   `--highvram/--lowvram/--novram`（已被移除）→ 照 usage 写脚本会直接 argparse 报错（本次实测踩到）

**反证（同一任务、同一参数，只换显存模式）**：去掉 `--highvram`（默认动态显存）后，
H3 以 `19995MB Staged` 动态装载，8 步采样正常推进（~46s/步），任务完成 → **根因确认为启动脚本参数**。

修复建议：
- `一键启动.bat` 默认不加 `--highvram`（动态显存是 12GB 卡跑大模型的唯一可行路径）；
  需要图像场景极致速度时另给一个"高显存模式"开关
- 增加 `一键启动_低显存.bat` 或参数化，并在 README 注明"视频任务勿用 --highvram"

**问题 P1-2（高）崩溃后无自愈 + 死引用**
- `comfy_agent/client.py:44-53` 连接失败时只给"请先通过绘世启动器启动 ComfyUI"文案，大脑无法执行该动作，
  只会把它转述给用户（实测交付语："我们需要先通过绘世启动器启动ComfyUI"）
- `comfy_agent/repair.py:236-237` 的 OOM 建议指向 `run_lowvram.bat`，而 `scripts/` 下**不存在该文件**
- 进程崩溃时引擎拿不到 history 条目，`parse_execution_error()` 无从解析，只能按"连不上"处理

**问题 P1-3（高）视频 OOM 无自动降参**
`comfy_agent/repair.py:212-233` 的 OOM 自动修复只处理 `EmptyLatentImage/EmptySD3LatentImage/EmptyLatent`
的 width/height 与 batch_size；而视频模板用的是 `MiniMaxH3ImageToVideo`/`EmptyMiniMaxH3LatentAV`/`LTXVImgToVideo`，
**一个都不匹配** → 只得 advice，需大脑自己改参数重跑（消耗 4 次执行预算之一）。

**问题 P1-4（高）同一视频两份评估、结论相反 → 触发无谓重生成**
实测事件序列：引擎强制评估 `video_forced → verdict=True, score=9`；紧接着大脑调 `view_video`
得到 `video → verdict=False, score=6`；于是大脑按"未达标"重写提示词并**再跑一次 5 秒视频**（~6 分钟 GPU）。
根因：两套评估各自独立——`comfy_agent/runner.py:212-244` 用**通用 criteria**（"视频帧质量良好…"）抽样 4 帧；
`brain/tools.py:359-397` 的 `view_video` 用**任务专属 criteria**。两者都发 `evaluation` 事件，
但大脑的上下文里没有"引擎已经评估过且通过"的信息，`runner.py:165` 的强制评估也不带 prompt_id 去重标记。
建议：二选一——引擎评估携带 `prompt_id` 供大脑跳过重复评估，或让引擎评估的 criteria 由任务提示词生成，
使两侧结论一致。

**问题 P1-5（中）模板承诺"音画同生"，实际产物无音轨**
实测 `ffprobe`：`agent_minimax_00006_.mp4` **没有音频流**（`-select_streams a` 无输出），
而提示词里明确写了"背景有鸟鸣声"，模板描述也写着"H3 支持音画同生"。
代码级根因：`comfy_agent/templates/video.py:138-143` 的采样链末端是
`VAEDecode`（只解码视频）→ `CreateVideo(images, fps)` → `SaveVideo`，
**整条链没有任何音频解码/合流节点**；`EmptyMiniMaxH3LatentAV` 产生的音频潜变量被直接丢弃。
建议：接 H3 的音频解码与 `CreateVideo` 的音频输入（或明确在模板描述里去掉"音画同生"表述）。

**前端体验问题（渲染期）**- **P2-1（中）长时间渲染零进度**：`comfy_agent/runner.py:122` 只在开始时发一次 `stage=running`；
  之后进度只靠 `brain/web/server.py:141-152` 的显存/队列轮询，而单任务 `queue_position` 恒为 1。
  实测 5-15 分钟里前端只有"阶段: running / 队列 1 / GPU xx GB"，工具卡停在"调用中…"。
- **P2-2（中）警告覆盖状态行**：`brain/web/static/app.js:604-609` 的 warning 分支直接把警告文本写进
  `#statusline` 且 `break`，不恢复阶段显示——用户会长时间看到"提示词维度不完整（缺 lighting）…"而非当前阶段。
- **P2-3（轻）重复评估卡**：`comfy_agent/runner.py:165` 对每个图像产物强制评估，
  大脑又按系统提示调 `view_image` → 每张图两张评估卡（实测 4 张图 6 张卡）。

---

## 四、并发隔离测试（视频渲染空档）

切回项目 A 发图任务，统计最近 15 秒的事件归属：

```
recentByProject: { "proj_8a4f0ee7": 444 }      # 当前项目
videoProjectEvents: 0                          # 视频项目串入事件
```

✅ **通过**：SSE 事件按项目隔离，切换项目后 DOM 无跨项目内容（`history.jsonl` 亦各自独立）。
附带证实了 P2-1：视频渲染期间该项目在这 15 秒内**零事件**。

---

## 五、测试手段自身的限制（非产品缺陷，但影响可自动化程度）

- IAB 的 Playwright 点击在本环境不可靠：`#newproject`/`#sendbtn` 的 `click()` 会 actionability 超时且不派发事件；
  坐标点击（`cua.click`）偶发不落地。测试改用页面内 `element.click()` / `requestSubmit()` 走**同一处理器路径**。
- `window.prompt()`（新建项目名）无法被 IAB 捕获（自动 dismiss→null），测试用页面内桩替代。
- 因此"按钮真实点击"这一层未获充分覆盖；上述替代均作用于同一 JS 处理器，业务路径等价。

---

## 六、结论摘要

**架构性优点（本次实测确认）**：多项目隔离严格、遮罩附件管线可用、局部重绘链完整、
参数护栏与提示词体检生效、启动脚本可自愈重启、模型跨设备适配在图像侧有效。

**必须修的三件事（按优先级）**：
1. **P0-1 模型名精确解析**（`model_adapt` 写回 canonical 名 + `validate` 建议用原始 choice）
   —— 当前每个图像任务都在无谓失败 2-4 次，且 `style_transfer`/`upscale_pass` 完全不可用
2. **P0-2 启动脚本去掉 `--highvram`**（或改成按任务切换）—— 否则视频模板在本机必崩
3. **P1-1 枚举修复回落模板推荐值**（而非字符串最接近）—— 避免静默改变采样器特性

**建议跟进**：崩溃自愈（引擎侧健康检查/重启）、视频 OOM 自动降参、渲染期进度事件、
评估卡去重、警告不覆盖阶段、`run_lowvram.bat` 死引用、日志追加而非覆盖、PID 文件校验。

---

## 七、未覆盖项（如实说明）

本轮已完成：A1-A4、B1（含崩溃取证与动态显存对比）、B2（大脑级失败 + 引擎级成功）、
B4（引擎级 10.33 秒核验）、B6（降级路径通过）、并发隔离。**未覆盖**：

- **B3 用末帧 i2v 续接**：渲染到 1/8 步时 GPU 达 87°C，按硬件保护中止（未取得结果）
- **B5 续第三段并合并到 15 秒**：因 B3 未完成 + 硬件保护，未执行；
  因此"15 秒完整链路"与"`run_count>4` 上限、单次 30 分钟等待超时"两条**未获实证**
- **A3 首次失败**（ckpt 名有效却报 `value_not_in_list`）的完整服务器报错被观测截断，**未完全定位**
- 大脑级的"取帧→续接→合并"全自动链路**未跑通**（B2 已证明大脑会漏上传步骤）

复测建议（全部 GPU 友好）：
1. 修 P0-1（模型名精确解析）后重跑项目 A 四项 → 预期各一次通过
2. 修 P0-2（启动脚本显存）+ P0-3（引擎代传输入文件）后，B2/B4 让大脑自由表述即可通过
3. 续接类（B3/B5）建议先接上温度熔断再测，并把单次帧数降到 60 帧以内以缩短满载时间
