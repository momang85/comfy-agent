# 视觉通道 + 本轮上传绑定（2026-09-12）

用户报告两个现象：「新设的 API 似乎没保存应用」+「LLM 看不到我新上传的图片，直接拿项目以前的图片做事」。
结论：**设置其实保存成功了**，坏的是看图那一路；而"看不到图"之后大脑又缺少"如实说看不到"的规则，
于是拿项目历史产物顶替了刚上传的图。

## 一、诊断（file:line 证据）

| 事实 | 证据 |
|---|---|
| 新配置**已保存**：`llm_base_url=https://tokenrhythm.studio/v1`、`llm_model=deepseek-flash`、`vlm_model=deepseek-flash`、新 key | `.comfy-agent/settings.json`；大脑「思考过程」能正常流式输出（走的就是这套） |
| 视觉地址读的是**导入期常量快照** | `comfy_agent/config.py:58` `VLM_BASE_URL = os.environ.get("VLM_BASE_URL", LLM_BASE_URL)`，而 `LLM_BASE_URL` 在导入时还没读 settings |
| 设置面板只有一个「API 地址」且只写 `llm_base_url`；服务端虽接受 `vlm_base_url` 但前端从不发 | `brain/web/static/app.js`（保存处理）、`brain/web/server.py::_post_settings` |
| 于是看图请求变成：**旧地址 z.ai + 新 provider 的模型名 + 新 key → 400** | `brain/llm.py:183,195`（`LLM HTTP <code>` 由 `_post` 抛出，body 截断 400 字符） |
| `VLMClient` 解析顺序里 `cfg.VLM_API_KEY` 排在 `settings.llm_api_key` 之前，`load_llm_api_key()` 会把它写成注册表 key → 大脑与视觉凭据串味 | 原 `brain/llm.py` VLMClient 分支 |
| 图像/视频评估也走 VLM，失败被 `except` 吞成 `pass=None`，`EvalResult.ok` 不算失败 → **评估一直静默跳过** | `brain/eval/base.py`（image/video 两处），`EvalResult.ok` |
| 「视觉模型」输入框打开时被清空、占位符是硬编码，用户看不到实际生效值 | `brain/web/static/app.js` `openSettings()` 里 `$("#set_vmodel").value = ""` |
| 大脑拿旧图的三条路径：① 每回合注入的「本项目最近产物」列表（全是旧图现成路径）② 历史消息里的旧上传注记 ③ `_resolve_upload` 按 0.75 相似度**静默替换**成另一张上传图 | `brain/agent.py`（recent_blob 注入）、`brain/tools.py::_resolve_upload`、`comfy_agent/runner.py::_ensure_inputs_uploaded`（裸名去产物目录找） |
| 没有任何规则要求"看图失败要如实告知、禁止编造/顶替" | `brain/agent.py` 规则 2 只是"先分析"的前置要求；规则 12 反而推动"换个工具继续" |

实测该 provider 的模型能力（256×256 探针 + 真实图片分析）：
`deepseek-flash` / `qwen3.8-max` / `qwen3.8-flash` / `kimi-k2.6` **支持视觉**；
`qwen3.7-max` / `glm-5.3` / `mimo-v2.5-pro` / `minimax-m2.7` / `deepseek-v4-pro-0813` 明确回
`MODEL_CAPABILITY_NOT_SUPPORTED vision`；不存在的模型名回 `MODEL_NOT_AVAILABLE`。
即：**用户原本的 `vlm_model=deepseek-flash` 是可用的**，400 完全由"地址走错供应商"造成。

## 二、修复

1. **视觉默认跟随大脑**（`brain/llm.py::VLMClient`）：`env VLM_* → settings.vlm_* → settings.llm_* → cfg` ；
   地址、Key、模型三条都是"先看视觉专用项、没有再跟随大脑"；去掉 `cfg.VLM_API_KEY` 抢在 settings 之前的串味。
2. **生效值可见**（`VLMClient.effective()` + `get_settings()` + 设置面板）：
   面板预填实际生效的视觉模型，并显示「视觉地址：跟随大脑（…）· 视觉 Key：跟随大脑 · 自检：✅/❌/⚠」；
   另加可选的「视觉 API 地址 / 视觉 Key」（留空=跟随大脑），供需要混合供应商时使用。
3. **启动/保存后自检**（`brain/web/server.py::vision_selfcheck` + `VLMClient.probe`）：
   用 256×256 探针图发一次真实请求；报错非瞬时 → 标"不可用"并推 warning；瞬时（5xx/网关/SSL）
   → 标"未确定"**不**告警（实测该 provider 常态抽风，据此宣布不可用会让人白改配置）；
   没报错但空回复 → "未验证"。探针图从 1×1 改成 256×256：1×1 会被部分 provider 以
   `Image dimensions are too small` 拒绝，把真视觉模型误判成不可用。
4. **本轮上传绑定**：`ToolContext.current_upload`（`Brain.handle` 写入），
   `analyze_image` 不传 path 就用它；`_resolve_upload` 改为**只认精确匹配**（抄错名 → 报错并列出候选，
   不再替换）；引擎 `_ensure_inputs_uploaded` 在图片参数为空时自动用本轮上传，裸名解析顺序改为
   **本轮上传 → uploads 精确 → outputs 精确**。
5. **规则 14（诚实性）** + 上传注记强约束 + 「最近产物」列表降级为"仅用户明确要基于旧产物时使用"：
   看图失败必须如实告知原因，禁止编造画面内容、禁止改用别的图片；改图/参考类请求停下并请用户文字描述。
6. **评估失败可见**：`EvalResult.vlm_error` + `_warn_vision_unavailable()`（按 地址|模型|错误 去重只提醒一次）；
   评估事件的 `vlm_error` 会显示在评估卡上（"⚠ 语义评估已跳过"），不再出现"评估通过"却什么都没查。

## 三、真机验证（2026-09-12 夜，同一台机器）

| 项 | 做法 | 结果 |
|---|---|---|
| 启动自检 | 重启 Web UI | `vision: deepseek-flash 跟随大脑`，`checked=True ok=True verified=True` |
| 瞬时报错不误判 | 首轮自检遇到 provider 502 | 标为"未确定"+ 不告警（改前会误报"不可用"） |
| 大脑真的看到新图 | 上传一张真实图 + "改成赛博朋克霓虹夜景，保持姿势构图" | `analyze_image` → `ok:true, source:本轮上传`，描述"银发红棕色眼睛、红围巾、浅蓝冬装、雪地森林"（与所传图一致） |
| 全链路 | 同上 | `style_transfer`（canny 锁姿势构图）→ 引擎评估 8/10 通过 → 交付；GPU 峰值 **76 °C**（护栏 88/92） |
| **看不到就说看不到** | 视觉模型临时改成 `no-such-model-xyz`（自检正确报 `MODEL_NOT_AVAILABLE`）后重发同一请求 | 大脑回复"这次**没能看到你新上传的这张图**"，给出原因，明确"不能编造画面内容，也**不能拿上一轮那张图顶替**"，请用户重试或文字描述；**未调用 run_template**（没有浪费一次渲染） |
| 恢复 | 视觉模型改回 `deepseek-flash` | 真实图分析恢复（描述准确），面板自检回到可用 |

单测：`tests/test_vision_binding.py` 新增 14 项（解析顺序 / env 优先 / effective 不吐 key /
probe 未配置 / 上传绑定 / 抄错名不替换 / 显式旧图仍可用 / 失败 hint 禁编造 / 引擎空参数填本轮上传 /
裸名优先 uploads / 评估 vlm_error 且只告警一次）。
全量 `python -m unittest discover -s tests` → **201 tests OK**。

## 四、本轮顺带修掉的自身回归

`run_template` 把 `current_upload` 透传给了 `run_workflow` →
`TypeError: run_workflow() got an unexpected keyword argument 'current_upload'`（真机测试第一轮就暴露）。
已在 `run_template` 入口 `kw.pop("current_upload", None)`，只传给 `_ensure_inputs_uploaded`。

## 五、已知边界

- 该 provider 的视觉探针/分析存在**间歇性空回复或 5xx**（实测 502/503/504/SSL EOF 都出现过一次）。
  自检因此区分"未确定"，但真实任务里仍可能出现一次失败 → 规则 14 会让大脑如实说明并请用户重试。
- `analyze_image` 只接受图片路径，不做内容安全兜底；被 provider 拦截时按规则 14 如实告知。
- 视觉模型与文本模型若属不同供应商，需在面板填「视觉 API 地址 / 视觉 Key」两项（留空即跟随大脑）。
