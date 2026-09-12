# 缺陷台账审计（逐条对照代码 · 2026-09-11）

方法：不采信文档自述，逐项在**当前代码**里核对修复是否真的存在。证据列为 file:line。
单测：`python -B -m unittest discover -s tests` → **148 tests OK**（2026-09-12 追加缺模型下载后为 **181 tests OK**，追加视觉通道/上传绑定后为 **201 tests OK**，架构反思与机制化后为 **238 tests OK**）。

## 一、状态总表

| ID | 问题 | 修复点（证据） | 状态 |
|---|---|---|---|
| P0-1 | 模型名分隔符致 `value_not_in_list` | `knowledge.py:235-259` `resolve_model_name`；`model_adapt.py:96-100` 写回 canonical；`validate.py:159` 建议用原始 choices | ✅ FIXED |
| P0-2 | 启动脚本 `--highvram` 崩视频 | `一键启动.bat:105-108` 默认空 + `COMFY_VRAM_MODE` 开关；`一键启动_高显存.bat` 存在 | ✅ FIXED |
| P0-3 | 视频 OOM 无自动降参 | `repair.py:214-230` 覆盖 `MiniMaxH3ImageToVideo/EmptyMiniMaxH3LatentAV/LTXVImgToVideo`（宽高×0.6、length//2） | ✅ FIXED |
| P0-4 | 输入文件未强制上传 | `templates/base.py:55-64` `INPUT_FILE_PARAMS`；`runner.py:334-372` 含裸文件名兜底 | ✅ FIXED |
| P0-5 | 渲染期无温度保护 | `guard.py:16-17` 两级阈值（WARN 88 / LIMIT 92）；`server.py:151-179`；`app.js:665-671` 显示温度 | ✅ FIXED |
| P1-1 | 枚举修复回落"最接近字符串" | `templates/base.py:98-132`（recommended→default→choices[0]） | ✅ FIXED |
| P1-2 | 同一产物双评估结论冲突 | `tools.py:420-430` 记录 `last_eval`；`:208-224` 复用；`runner.py` 事件带 `prompt_id`；`app.js:554-566` 去重 | ✅ FIXED（小缺口见下） |
| P1-3 | 崩溃无自愈 + 死引用 | `client.py:97-108`（可执行文案 + `is_alive`）；`repair.py:253-254`；`guard.py:56-94` AUTO_START | ✅ FIXED |
| P1-4 | `use_last` 只看最近一次运行 | `tools.py:191-205` `_latest_project_outputs`（`:261`/`:448` 使用） | ✅ FIXED |
| P1-5 | "音画同生"名不符实 | `video.py:76`、`brain/skills/video.md:11-12` 已改表述；音频**未实现** | ⚠️ PARTIAL（由本轮 LTX 音频链解决） |
| P1-6 | 任务语义漂移 | `brain/agent.py:310` 规则 12（①修参数→②换工具→③降级并告知） | ✅ FIXED |
| P2-1 | 渲染期零进度 | `server.py:184-207`（`elapsed_sec`）；`app.js:673-680` | ✅ FIXED |
| P2-2 | 警告覆盖状态行 | `index.html:23` `#warnline`；`app.js:541-552`/`:629-632` | ✅ FIXED |
| P2-3 | 评估卡重复 | `app.js:554-566`（按 `prompt_id`） | ✅ FIXED |
| P2-4 | 启动脚本工程化 | `一键启动.bat:23`（路径规范化）、`:26,189-197`（探测）、`:54-62`（PID 身份）、`:108,132,199-205`（追加+轮转） | ✅ FIXED |
| E1 | 缺 `comfy_root.local` 直接失败 | 同上探测逻辑 | ✅ FIXED |
| E2 | 过期 `webui.pid` 可能误杀 | `一键启动.bat:54-62` + `server.py:534-536,555` | ✅ FIXED |
| E3 | 日志截断丢崩溃现场 | `一键启动.bat:108,132`（`>>`）+ `:199-205`（5MB 轮转） | ✅ FIXED |
| 新 | `list_outputs` 工具 | `tools.py:227-249`，注册 `:801` | ✅ FIXED |
| 新 | 视频 length 超训练区间 | `video.py:93`(362)、`:191`(257)，`:55-60` 收敛 | ✅ FIXED |
| 新 | AUTO_START_COMFY | `runner.py:51-61`；`guard.py:56-94`（默认关） | ✅ FIXED |

## 二、仍未解决 / 部分（本轮处理或登记）

| # | 项 | 现状 | 本轮处理 |
|---|---|---|---|
| 1 | **音频未实现**（P1-5 实质部分） | LTX 链无音频分支 | ✅ 本轮随 LTX 修复一起做（`LTXVAudioVAELoader`/`LTXVEmptyLatentAudio`/`LTXVAudioVAEDecode`/`CreateVideo.audio`） |
| 2 | **LTX 设计风险**（已证实是 bug） | `video.py:220-226` 用 checkpoint 槽1 当 CLIP；本机有专用 `LTXAVTextEncoderLoader`；正负接同一路；缺 `LTXVConditioning` | ✅ 本轮修复并实测 |
| 3 | **LTX 8k+1 帧对齐** | 仅上界护栏；"5s"→120 帧不合格 | ✅ 本轮加 GRID 对齐 |
| 4 | **B5 时长核对缺口** | `brain/eval/base.py:143-192` 算了 `duration` 却丢弃；`merge_videos` 无 `target_seconds` | 登记；若顺手回传 `duration_sec` |
| 5 | 按任务类型的温度阈值 | `guard.py:16-17` 单套阈值 | 登记（图像任务 50-60°C 也被同一线约束） |
| 6 | brain 自评事件缺 `prompt_id` | `tools.py:289,471` 未带 | 登记（引擎事件已带，复用路径已覆盖常见情况） |
| 7 | `guard.py:8` 文档漂移（写"默认 85"） | 代码已是 88/92 | 本轮顺手改 |
| 8 | 测试可观测性：A2 的 template_id 未记录 | 事件窗口限制 | 登记（非代码缺陷） |

## 三、结论

台账 24 项中 **21 项已修复且有代码证据**，2 项属"按设计仅改表述/登记待办"（音频、时长核对），
1 项（LTX 设计风险）经代码签名核实为**真 bug**，随本轮 LTX 修复解决。除登记项外无回归。

## 四、2026-09-12 追加：缺模型下载功能实测暴露的缺陷

详见 `docs/missing-model-download-2026-09-12.md`。四条均由本轮**真机真网络**测试发现：

| ID | 问题 | 根因 | 修复点 | 状态 |
|---|---|---|---|---|
| D1 | `COMFY_ROOT` 形态混用：`comfy_root.local`/`一键启动.bat` 存整合包根（`…-v3`），`config.py` 期望 ComfyUI 目录（`…-v3\ComfyUI`）→ `MODELS_DIR`/`MANAGER_CACHE`/`INPUT_DIR`/`OUTPUT_DIR` 全部指向不存在路径，`knowledge._load_manager_extmap()` 静默返回 `{}` | 启动脚本与配置各自定义 COMFY_ROOT，无归一 | `config.py:_normalize_comfy_root()`（两种形态都认）；回归测试 `test_settings.py::test_comfy_root_accepts_portable_root` | ✅ FIXED |
| D2 | 大脑自拼 HuggingFace 地址（实测 401）并自估大小（4.71MB → 1.2GB） | 工具未强制"照抄候选"，无二次校验 | `tools.py:_pick_cached_candidate` + `url_corrected`；`model_download.request` 弹窗前 HEAD 探测、差异 >20% 写入 `warnings`；规则 13 明确禁止 | ✅ FIXED |
| D3 | 下载校验以"登记大小"判偏小 → 大脑估错时误杀正确文件 | 基准取错 | `_run` 改用本次传输的 `Content-Length`（权威） | ✅ FIXED |
| D4 | 候选文件名匹配过宽：`definitely-not-a-real-model-xyz.safetensors` 命中 `model.safetensors` | `_score_filename` 双向子串判据 | 改为同名/前缀/公共前缀占比 | ✅ FIXED |
| D5 | 单测真出网 + `HTTPError` 未关闭（ResourceWarning 401） | 测试未隔离网络；异常分支未 `close()` | 测试注入假 resolver/`_head_size`/`_open_stream`；`_head_size` 的 `HTTPError` 分支 `e.close()` | ✅ FIXED |

## 五、2026-09-12 追加：视觉通道 + 上传绑定（详见 `docs/vision-channel-and-upload-binding-2026-09-12.md`）

| ID | 问题 | 根因 | 修复点 | 状态 |
|---|---|---|---|---|
| D6 | **视觉请求发往旧 provider**：用户改 API 地址后看图必 400 | `config.py:58` `VLM_BASE_URL` 是导入期常量快照；设置面板只写 `llm_base_url` | `VLMClient` 解析改为 `env → settings.vlm_* → settings.llm_* → cfg`；面板预填生效值 + 显示"跟随大脑/自检" | ✅ FIXED |
| D7 | 面板「视觉模型」永远空白 + 硬编码占位符；`get_settings()` 不返回视觉生效值 | `app.js openSettings()` 清空该字段；服务端只返回 `LLMClient().masked()` | 预填 `vision.model`；`get_settings()` 增 `vision`/`vision_state` | ✅ FIXED |
| D8 | 视觉不可用**无任何用户可见信号** | 无自检；`VLMClient` 也未暴露生效配置 | `VLMClient.effective()/probe()` + `vision_selfcheck()`（启动与保存后各一次，失败推 warning，瞬时只标"未确定"） | ✅ FIXED |
| D9 | 大脑拿项目历史产物顶替本轮上传的图 | 无 `current_upload` 状态；每回合注入最近产物（全是旧图）；历史注记长期存活 | `ToolContext.current_upload` + `analyze_image` 默认用它 + 引擎空参数自动填 + 产物列表降级标注 | ✅ FIXED |
| D10 | `_resolve_upload` 按 0.75 相似度**静默替换**成另一张上传图 | 相似度纠错（本意是修笔误） | 改为只认精确匹配；找不到则报错并列出真实候选 | ✅ FIXED |
| D11 | 看图失败后大脑编造画面内容（"根据历史记录这张图与之前相同"） | 提示词只有"先分析"的前置要求，缺失败分支的诚实性规则 | 新增规则 14 + 上传注记强约束 + 任务映射补充 | ✅ FIXED |
| D12 | 视觉失败时评估静默跳过，还显示"评估通过" | `except` 吞掉 → `pass=None`，`EvalResult.ok` 不计失败 | `EvalResult.vlm_error` + 去重告警 + 评估卡显示"语义评估已跳过" | ✅ FIXED |
| D13 | 自查回归：`current_upload` 透传给 `run_workflow` → TypeError | 新参数未在 `run_template` 入口取出 | `kw.pop("current_upload")`（真机第一轮即暴露） | ✅ FIXED |

## 六、2026-09-13 追加：项目 5 暴露的结构性成因（详见 `docs/architecture-reflection-2026-09-13.md`）

| ID | 结构性问题 | 机制化修复 | 状态 |
|---|---|---|---|
| D14 | 世界镜像过期无人对账：快照无 TTL、会话共用、判"存在"只查缓存 | `comfy_agent/world.py`（`ensure_fresh`/`model_exists` 严格同名/`file_on_disk` 落盘兜底）+ `refresh_world` 工具 + `/api/refresh` + 前端重扫按钮；下载完成强制刷新 | ✅ FIXED |
| D15 | 模糊匹配当"存在"：`new.safetensors` 命中 `old.safetensors`（0.48） | `Knowledge.model_exists` 严格判定；`find_model` 门槛 0.35→0.6 且只用于建议 | ✅ FIXED |
| D16 | 服务器拒绝枚举时用陈旧快照 `choices[0]` 顶替（静默换模型） | `_apply_server_fixes` 改取**服务器实时选项**，拿不到就不替换并交回大脑 | ✅ FIXED |
| D17 | 用户要求不进判据：特写拿 9/10 | `brain/task.py::TaskContract` → 评估 criteria + 交付对照；实测新判据给出 `verdict=false score=3` | ✅ FIXED |
| D18 | 只改 denoise 即绕过重复护栏，同一基底重绘 3 次 | `AttemptLedger`（签名含模板+基底+手法，不含 denoise）+ 引擎层拦截；实测第 2 次同手法 `blocked=true` | ✅ FIXED |
| D19 | 失败不分类，缺节点被当成缺模型白下 21.46MB | `brain/policy.py` 分类表 + `download_model` 返回 `consumer/usable`；缺加载节点时明说"下载无用" | ✅ FIXED |
| D20 | 重跑消息前提编造（"刚才因缺模型失败"） | `missing_retry` 只在 runner 真因 `missing_models` 早退时写入；无记录时只中性告知已就绪 | ✅ FIXED |
| D21 | 异常让整轮没有回复且无痕迹（真机复现 SSL EOF） | `handle()` 包住工具循环：任何异常都产出"解释 + task report + turn_end"；LLM 瞬时故障退避重试 1 次；审计写失败改为可见 | ✅ FIXED |
| D22 | `validate` 带阻断错误却 `ok:true`；尺寸参数被静默忽略 | `tool_validate` 有 blocker 即 `ok:false`；尺寸类未知参数**拦在执行前**并给替代参数名 | ✅ FIXED |
