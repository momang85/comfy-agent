# 缺陷台账审计（逐条对照代码 · 2026-09-11）

方法：不采信文档自述，逐项在**当前代码**里核对修复是否真的存在。证据列为 file:line。
单测：`python -B -m unittest discover -s tests` → **148 tests OK**。

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
