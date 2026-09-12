# 架构反思：把"必然产生无用消耗"的结构改掉（2026-09-13）

针对项目 5（`proj_ef2d127d`，2026-09-12 20:13→23:55，9033 行事件）暴露的问题，这次不从症状入手打补丁，
而是找出**让浪费必然发生**的结构性成因，用机制消除。本文是这次反思的结论与落地记录。

## 一、五条结构性成因（都带证据）

### 成因 1｜代理维护一份会过期的"世界镜像"，且没有对账责任方
节点签名/模型枚举来自磁盘快照（`knowledge.py` 旧实现只看"文件存在"，无 TTL、无 mtime 校验），
文件清单来自 `build()` 那一刻的 `/models`，整个 Web 会话共用一份（`brain/tools.py` 的 `ToolContext`）；
`find_model/resolve_model_name/checkpoint_exists` 只查缓存、**从不看盘**。
后果：下载了模型/装了节点/改了目录，系统仍用旧镜像判"缺"；服务器报 `value_not_in_list` 时
还拿**陈旧快照的 `choices[0]`** 顶上 → 静默换模型。

### 成因 2｜"用户要什么"从未变成机器可检查的判据
需求只以散文存在于对话里，强制评估用通用套话（"画面清晰完整、主体明确"）。
项目 5 的"全身照"从未进入任何判据 → 特写拿 9/10（20:13:39），用户 20:15 直接反驳"你这并不是全身照"。
**做得对不对无法判定，就只能靠重绘碰运气。**

### 成因 3｜没有计划、没有预算、失败不分类，止损靠提示词
护栏只有"完全同参重复"（`brain/agent.py` 旧实现）与 `run_count>4`；改 denoise 即换签名
→ 同一基底图连着重绘 3 次（23:50:34/23:51:32/23:52:03，分数 6→4→6→6）；
`repairs:[]` 说明引擎的自动修复只管校验/OOM，语义不收敛无人管；"要修手"没有机制逼它走局部。

### 成因 4｜能力可用性在执行后才被发现
13 次 `search_nodes/inspect_node/learn_node`（23:53–23:55）才发现本机没有 `UltralyticsDetectorProvider`，
而 21.46MB 的 `hand_yolov8s.pt` 已经下完（23:53:15）。下载决策只看"能不能下"，不看"本机有没有能加载它的节点"。

### 成因 5｜回合状态与工具语义不可信，收尾靠运气
- `validate` 返回 `ok:true` 却带 `bad_enum`（23:55:25），大脑据此认为"接线正确"；
- 重跑消息的前提是编造的：`_download_finished` 拿 `ctx.draft_meta` 的"最后一次 render"当
  "刚才因缺模型失败的任务"，而日志里 `model_missing` 事件为 0、11 次 render 全部 completed；
- **某轮压根没有回复**（23:50:30"要修手"那一轮 0 条 delivery）。本轮复现并定位了机制：
  provider 的 SSL EOF 让 `handle()` 直接抛出，既没有交付也没有落盘痕迹（本次真机复现：
  `last_error: LLMError: 无法连接 LLM 服务…SSL: UNEXPECTED_EOF_WHILE_READING`，且 task_reports 为空）。

## 二、落地的六个机制

| 机制 | 文件 | 解决成因 | 关键行为 |
|---|---|---|---|
| **WorldModel**（世界模型 + 新鲜度） | `comfy_agent/world.py` | 1 | `ensure_fresh` 按 TTL 或 `MODELS_DIR` mtime 重取；`model_exists` **严格同名**（模糊匹配不许判"存在"）；`file_on_disk` 落盘兜底；`loader_for` / `model_usability` / `route_available` 回答"谁能加载它"；`live_choices` 拿服务器实时选项 |
| **TaskContract**（可检查的成功判据） | `brain/task.py` | 2 | 从用户话里正则抽取约束（全身/保持构图/画风一致/修手…）→ 进评估 criteria、进交付对照 |
| **AttemptLedger**（策略台账 + 止损） | `brain/task.py` | 3 | 签名 = 模板 + 基底图 + 手法（**不含 denoise/seed**）；第 2 次要求换手法、再坚持即硬拦；分数不提升判定 |
| **FailurePolicy**（失败分类 → 动作白名单） | `brain/policy.py` | 3、4 | `config/missing_asset/missing_node/transient/semantic/budget/user_input`；只有 transient 允许重试，其余各自规定动作 |
| **CapabilityPlan**（下载前查清用途） | `brain/tools.py` + `world` | 4 | `search_models`/`download_model` 返回 `consumer{loader,present,usable,note}`；缺加载节点时明说"下载解决不了问题" |
| **回合不变量 + 可观测性** | `brain/agent.py`、`brain/events.py`、`brain/web/server.py` | 5 | 任何异常都变成"一句解释 + 一条 task report + turn_end"；LLM 瞬时故障退避重试 1 次；`missing_retry` 只在真因缺模型失败时写入；`validate` 有 blocker 即 `ok:false`；审计写失败会打印而不是静默 |

引擎侧同时收紧了三个"静默"：尺寸类参数写错**拦在执行前**（不再"渲染完才发现 width/height 被忽略"）、
服务器枚举修复以**实时清单**为准（拿不到就不替换）、缺模型判定走严格同名+落盘。

## 三、真机验证（2026-09-13，项目 5 原图 + 原请求）

请求："生成一个差不多风格的动漫少女全身照"（上传同一张图）。

| 观察项 | 修复前（09-12） | 修复后（09-13） |
|---|---|---|
| 评估判据 | 通用套话 | `必须全身：从头到脚完整可见（含脚/鞋）；画风与参考图一致` |
| 首轮评估结论 | `verdict=true score=9`（用户随后反驳） | `verdict=false score=3`（**如实说不达标**） |
| 重复手法 | 同一基底连着重绘 3 次（6→4→6→6） | 第 2 次同手法被引擎拦下（`blocked=true`），大脑改换 t2i |
| 最终结果 | 用户要纠正才有全身照 | t2i 一次通过 `verdict=true score=8` |
| 回合收尾 | 某轮 0 条 delivery、无痕迹 | `turn_end` + `task_reports/task_20260913_012924.json`（renders=3, wasted=1） |
| 报告内容 | 无 | 契约、三次尝试与分数、拦截次数、phase=delivered |

日志证据（新）：`task_reports/task_20260913_012924.json` 里可见
`i2i(score=3) → style_transfer(score=3) → t2i(score=8)`，`wasted=1`。

## 四、测试（238 全绿，其中三道新闸门）

- `tests/test_task_integrity.py`（27 项）：世界模型新鲜度/落盘兜底/加载链路/严格判定、
  契约解析、台账止损（**仅改 denoise 必须被拦**）、失败分类表、引擎拦截（尺寸参数、实时枚举、validate blocker）。
- `tests/test_project5_replay.py`（9 项，**关键闸门**）：把项目 5 日志里真实发生的输入喂给新决策层，断言
  ① 三次真实重绘里第 3 次被拒（当时渲染了 3 次，现在 1 次）② "全身照"进判据且 VLM 不可用不得报通过
  ③ 下载前就报出"缺 UltralyticsDetectorProvider" ④ 不编造"因缺模型失败的任务" ⑤ 有待确认弹窗仍交付 ⑥ 空参数给可读错误。
- 真机端到端：上文第三节。

## 五、还没有做的（诚实登记）

1. **自动走局部修复**：台账会拦"整图重绘"，但"修手"要真正落地还需要遮罩来源（用户提供 / MaskRectArea /
   检测器）。本机情况是：`AILab_YoloV8Adv` 能加载 ultralytics 模型（RMBG 包），
   但 Impact 的 `UltralyticsDetectorProvider` 没注册（包在、节点不在 → 多为依赖没装成功，需查 ComfyUI 启动日志）。
2. **CapabilityPlan 目前只覆盖"下载前查用途"**，还没有把"目标 → 候选链路 + 成本估计"完整前置到开工时。
3. 项目 5 里"某轮没有 delivery"的根因这次复现为 LLM 异常逃逸；已加不变量，但**未能证明** 09-12 那次也是同一原因
   （日志缺失，无法回溯）。
4. `find_model` 的模糊匹配虽然不再用于"存在性判断"，但仍在给建议时使用；阈值 0.6 是否合适需要更多真实样本。
