# ComfyUI 全能 Agent（comfy-agent）

自然语言驱动的 ComfyUI 生成助手：**大脑**（ZCode 式 agent 循环，OpenAI 兼容 BYOK）+ **执行引擎**（验证-修复闭环，纯标准库）双层架构。

```
你（中文自然语言，可带图片）
   ↓
brain/  大脑：理解需求 → 选模板 → 写提示词 → 调工具 → 看结果 → 迭代
   ↓ 进程内调用
comfy_agent/  引擎：模板渲染 → 本地校验 → 自动修复 → 提交执行 → 下载产物
   ↓ HTTP（仅限本机回环）
ComfyUI (127.0.0.1:8188，绘世启动器启动)
```

## 快速开始

### 0. 前置
- 一台装了 ComfyUI（0.3x）的机器，ComfyUI 运行在 `127.0.0.1:8188`
- Python 3.10+（ComfyUI 整合包自带 Python 即可），**零第三方依赖**（纯标准库）
- 一个 OpenAI 兼容的 LLM API key（智谱 z.ai / DeepSeek / Kimi / OpenAI 等均可）

### 1. 三步安装（第一次使用）

**Windows**：
1. 双击 `scripts\install.bat`，输入你的 ComfyUI 安装路径（含 `python\python.exe` 的目录）——写入 `comfy_root.local`（本机配置，不入 git）
2. 双击 `scripts\一键启动.bat`：自动启动/复用 ComfyUI 与大脑 Web UI，浏览器打开 `http://127.0.0.1:8899`
3. 点页面右上角 **⚙**，填入你的 API 地址与 Key（key 只存本机 `.comfy-agent/settings.json`，不上传不落库）→ 保存即生效

**Linux / macOS**：设置环境变量 `COMFY_ROOT`、`LLM_API_KEY` 后 `python -m brain --web`（同端口 8899）。

### 1.5 在别人的电脑上运行（可移植性）

项目按"换台机器就能跑"设计，前提只有三个：

| 前提 | 说明 | 不满足会怎样 |
|---|---|---|
| Python 3.10+ | **零第三方依赖**（纯标准库），ComfyUI 整合包自带的 python 也行 | 无法启动 |
| ComfyUI 0.3x 已启动（`127.0.0.1:8188`） | 任何安装方式都行；模型库可以不同 | 生成失败（报"服务器连不上"） |
| 一个 OpenAI 兼容 LLM key | 智谱/DeepSeek/Kimi/OpenAI 任一 | 大脑不出结果 |

其他全部自动适配，无需改代码：

- **路径零硬编码**：工作区/产物/设置都基于项目根目录推导；ComfyUI 目录经 `install.bat`（Windows）或 `COMFY_ROOT` 环境变量指定
- **模型名自动适配**：模板默认模型（novaAnimeXL 等）本机不存在时，图像模板自动绑定你本机的同家族 checkpoint（`model_prefs` 可指定偏好）；视频模板才需要按名安装对应模型
- **ffmpeg 可选**：仅视频抽帧评估用到，`FFMPEG_PATH` 环境变量或 PATH 里能找到即可
- **无注册表依赖**：API key 读取链为 环境变量 → settings.json → （Windows）注册表，非 Windows 环境跳过注册表

Windows 用户：`install.bat`（填一次 ComfyUI 路径）→ `一键启动.bat` → ⚙ 填 key，三步完成。
Linux/macOS 用户：`COMFY_ROOT=/path/to/ComfyUI LLM_API_KEY=sk-... python -m brain --web`（无需 bat 脚本）。

### 2. 引擎层（无需 LLM）

```bash
cd comfy-agent
python -m comfy_agent.cli status            # 服务器状态
python -m comfy_agent.cli templates         # 模板目录
python -m comfy_agent.cli run t2i --json '{"prompt":"masterpiece, 1cat, orange cat, astronaut helmet","width":1024,"height":1024}'
python -m comfy_agent.cli upload /path/to/img.png
python -m comfy_agent.cli convert 某工作流.json --out api.json
```

### 3. 大脑（Web UI 或命令行）

```bash
python -m brain --web              # Web UI（推荐，⚙ 面板配置 key）
python -m brain "画一只赛博朋克橘猫，1024x1024，4张"   # 一次性任务
```

配置优先级：环境变量 > ⚙ 面板设置（settings.json）> 默认值。大脑会：写好英文标签提示词 → 本地校验 → 执行 → VLM 看图评估 → 不达标自动调参重试一次 → 交付中文总结+图片路径。

### 隐私说明
- 你的对话记录、技能记忆、产物全部存在本机 `.comfy-agent/` 目录（已被 git 忽略，不会进入仓库）
- API key 仅存本机 `settings.json`（gitignored）；前端界面只显示脱敏尾号
- 图片/视频/工作流文件不上传任何第三方，只在本机 ComfyUI 与你自己配置的 LLM 服务之间流转

## 朋友使用指南（拿到代码后 5 分钟跑通）

### 0. 前置
- ComfyUI 已装好且能启动（0.3x；绘世整合包或手动安装均可）
- `ComfyUI/models/checkpoints/` 里至少有一个 **SDXL 或 SD1.5 checkpoint**（任何模型都行；没有就从 Civitai / HuggingFace 下载一个 `.safetensors` 放进去）
- 一个 OpenAI 兼容 LLM API key（智谱 z.ai / DeepSeek / Kimi / OpenAI 任一）
- 用视频模板另需对应模型文件（见下方「模型要求」）

### 1. 三步安装
按上面「快速开始」：`install.bat` 填一次 ComfyUI 安装路径 → 双击「一键启动.bat」→ 右上角 ⚙ 填 key。

### 2. 首次使用（直接说中文）
| 你说的话 | 发生什么 |
|---|---|
| "画一只戴着红色围巾的小狐狸" | 自动选模板 → 写英文提示词 → 出图 → VLM 评估 → 中文交付 |
| 📎 传一张图 + "改成吉卜力风格" | 先看图 → i2i/style_transfer → 按评估迭代 → 交付 |
| "画一只猫，然后放大两倍" | compose 管线一次提交两段执行 |
| 顶栏切换项目 | 不同需求分项目，记忆与产物互不干扰 |

### 3. 模型要求（模型名不一样也能跑）
| 模板 | 需要你装什么 | 适配 |
|---|---|---|
| t2i / i2i / upscale_pass | **任意** SDXL 或 SD1.5 checkpoint | ✅ 自动适配：模板默认模型（novaAnimeXL 等）本机不存在时，自动绑定你本机的同家族模型，无需改任何配置 |
| style_transfer | 任意 SDXL checkpoint + ControlNet 文件 | checkpoint 自动适配；ControlNet（canny）需 `controlnet++_union_sdxl_promax.safetensors` |
| minimax_t2v / minimax_i2v | MiniMax H3 unet + turbo LoRA + qwen3vl + video vae | 视频模板按文件名匹配，需原名安装 |
| ltx_i2v | LTX-2.3 checkpoint + LoRA + 本地 Gemma 编码器 | 同上 |

- **适配优先级**：`settings.json` 的 `model_prefs`（如 `{"sdxl": "我的模型.safetensors"}`）> 同家族模型 > 任意 checkpoint；适配成功时对话里会提示"已自动适配"
- 想指定偏好模型：编辑 `.comfy-agent/settings.json` 加 `model_prefs` 字段即可
- 视频/ControlNet/LoRA 的具体文件名见 `comfy_agent/templates/video.py`、`image.py` 顶部常量

### 4. FAQ
- **我的模型名和 README 里不一样，会失败吗？** 图像模板不会——运行期自动绑定本机 checkpoint；只有视频模板按文件名找模型，需按原名安装。
- **没有 NVIDIA 卡 / 显存小？** ComfyUI 低显存模式或 CPU 模式都能跑通全流程（慢一些）；执行期 OOM 时引擎自动降分辨率/批数并重试一次。
- **key 安全吗？** 只存本机 `.comfy-agent/settings.json`（gitignored），只发给你自己配置的 LLM 地址；对话、记忆、产物全在本机。
- **能用别的 LLM 吗？** 任意 OpenAI 兼容服务（DeepSeek/Kimi/OpenAI/本地 Ollama）都行，⚙ 面板改 base_url 与模型名，保存即热生效。
- **连朋友机器上的 ComfyUI 可以吗？** 默认只连本机 127.0.0.1（SSRF 防护）；确有需要设 `COMFY_ALLOW_LAN=1` 再改 `COMFY_URL`。

## 模板库（图像模板自动适配本机 checkpoint）

| ID | 名称 | 模型 | 来源 |
|---|---|---|---|
| t2i | 文生图(默认SDXL) | 任意 SDXL（自动适配）；支持 hires 潜空间二段放大、style_prompt 独立编码 | 手写 |
| t2i | 文生图(轻量) | 任意 SD1.5（ckpt 参数切换） | 手写 |
| i2i | 图生图 | 任意 SDXL（自动适配） | 对齐用户已验证工作流 |
| style_transfer | 风格转绘 | 任意 SDXL+ControlNet | 对齐用户已验证工作流 |
| upscale_pass | 高清放大 | 任意 SDXL + tiled VAE | 管线拼接用 |
| inpaint | 局部重绘 | 原图+遮罩（零额外模型） | 手写 |
| minimax_t2v | 文生视频 | MiniMax H3+Qwen3VL+turbo | 从本机节点签名构建 |
| minimax_i2v | 图生视频 | 同上+首帧 | 同上 |
| ltx_i2v | 图生视频 | LTX-2.3+本地Gemma | 对齐用户12GB优化工作流 |
| merge_videos / extract_frame | 视频拼接/取末帧 | VHS | 多段续接用 |

## 诊断与能力索引

```bash
python -m comfy_agent.cli health         # 连线健康审计：全部模板+能力骨架逐图校验（含连线类型）、1527 节点端口类型覆盖率、能力可用性
python -m comfy_agent.cli capabilities   # 能力→节点审计表（节点+模型双重核验，自动降级建议）
```

- 大脑系统提示内置"能力→节点偏好"表（运行时按本机节点与模型核验）：修脸/锁姿势/锁构图/放大/局部重绘等 13 项能力，每项标注本机可用链路或降级路线
- 提示词体系：`brain/skills/prompts.md` 分块结构+词库（图像八段式、MiniMax H3 官方字段式视频脚本）；引擎侧分块覆盖体检 + 负面词保底合并（只补不删）

## 抓住的三个空白点（对应市场调研报告）

1. **执行-修复闭环**：本地预校验（节点/枚举/范围/文件，对照本机 1527 个节点签名）→ 服务器结构化报错解析 → 参数级修复 → 重试≤3次。实测：用户旧工作流因 VAE 文件丢失/节点改名而失效，自动完成"checkpoint 自带 VAE"结构级修复后成功执行。
2. **节点知识检索层**：`/object_info` 快照（含 input_order 权威顺序）+ Manager 缓存 35390 条节点→包映射，模糊检索带**同家族约束**（SDXL VAE 不会被"修复"成 MiniMax 音频 VAE）。
3. **中文市场**：全中文模板名/参数/诊断/skill 文档。

## 安全设计

- ComfyUI 连接默认仅限回环地址（SSRF 防护），连其他机器需显式 `COMFY_ALLOW_LAN=1`
- LLM 外部服务强制 https；本地推理（Ollama）仅回环 http；阻断云元数据地址
- CLI 文件读取限制在允许目录（ComfyUI 目录/工作区/桌面/下载），禁止 `..` 穿越
- 错误修复永不静默：每次自动修复都记录在 `repairs` 报告中

## 目录结构

```
comfy_agent/          执行引擎（纯标准库，可独立使用）
  client.py           HTTP 客户端（/prompt /history /upload /object_info ...）
  knowledge.py        节点知识索引（快照+Manager缓存）
  convert.py          UI→API 格式转换器（工作流 agent 化基建）
  validate.py         本地预校验
  repair.py           自动修复（含 OOM 降参、同家族模型匹配）
  model_adapt.py      跨设备模型自动适配（默认 checkpoint 缺失→绑定本机同家族模型）
  promptspec.py       提示词家族规范（SDXL标签/LTX英文/MiniMax中文）
  templates/          7 个模板
  runner.py           执行编排
  cli.py              引擎 CLI（JSON 输出）
brain/                大脑
  llm.py              OpenAI 兼容 BYOK 客户端 + VLM
  agent.py            think→act→observe 主循环
  tools.py            13 个领域工具
  eval/               分层评估（Tier0 确定性 / Tier2 云端 VLM / 策略路由）
  skills/             领域知识（图像/视频参数经验）
  chat.py             入口
tests/                11 个单测（转换对齐/家族安全匹配/修复/模板/评估）
```

## 评估策略（EVAL_POLICY）

- `auto`（默认）：Tier0 确定性检查每次跑（免费）→ 成图走云端 VLM **区域级诊断**（issues[].location/fix_hint，可映射到具体参数修改）
- `local`：仅 Tier0（本机 VLM 评估为 Phase 2 扩展槽）
- `off`：不自动评估

## 多模态输入闭环（Phase 2 第一弹）

- **先看再干**：任务涉及图片时，大脑先 `analyze_image`（glm-4.6v 分析内容/风格/构图+推荐模板与提示词）再动手——改图任务不再盲猜
- **技能沉淀**：每轮任务的成功轨迹（含失败→修复→成功）自动写入 `.comfy-agent/sessions/skills.json`；新任务按关键词重叠度（中文二元组分词）召回相似经验注入大脑——同类任务第二次起直接走对的路
- **硬性护栏**：单任务最多执行 4 次生成（首次+3次修复），防止无限烧 GPU；评估结论（pass_overall）与工具成败（ok）分离，大脑不再误判

## 工作流图合成引擎（受限图合成）

模板覆盖不了的需求由 `comfy_agent/synth/` 合成引擎接管（验证引导小步编辑路线）：

- **管线拼接 compose**：多模板在类型化端口串联（"画一只猫然后放大两倍"= t2i→upscale_pass 一条流水线，一次提交两段执行）。入口节点删除法重接下游——兼容 ComfyUI 0.33（LoadImage 已不接受连线传入 IMAGE）
- **单节点插入 patterns**：`add_lora`（checkpoint→LoRA→消费方重接线）、`add_controlnet`（Canny 离线安全）
- **LLM 图编辑会话**：`synthesize` 开会话 + `propose_edit` 小步提交编辑批量（add_node/connect/set_input/insert_lora/insert_controlnet）→ 每步类型校验+DAG环检测 → **拒绝即整体回滚+结构化诊断**（被拒编辑不污染图）
- 实测：compose 管线真实执行两段产物（t2i 1024² + 放大 2048²）；ControlNet 锁构图任务 5 个工具调用干净交付

## 节点语义知识体系（新节点也能懂）

- **learn_node**：LLM 按需生成节点语义档案（中文用途/每个输入怎么接/输出接什么/典型接线/坑）并持久缓存——依据是 object_info 签名（类型/枚举/默认值），**任何新装的节点都能当场生成正确档案**，不依赖官方文档
- **search_nodes**：语义搜索（支持中文概念别名：放大→upscale、采样→sampler、人脸→face...），1527 个节点全量可查
- **技能库注入**：`skills/core-nodes.md`（核心节点速查）+ `skills/workflow-design.md`（从零建图方法论）全量注入大脑系统提示

## 从零建图（自由合成）

`scaffold(t2i/i2i)` 搭骨架 → 逐环节 `propose_edit` → 不认识的节点 `learn_node` → 被拒看诊断修改。引擎侧关键设计：

- **编辑宽容归一化**：LLM 的 `{"add_node": {...}}` / 嵌套式 / 标准式全部接受，节点 id 缺省自动分配
- **中间态合法**：分步建图时"连线未接"的节点不算阻断（pending_wiring），最终 submit 兜底全量校验
- **拒绝即回滚**：批量编辑原子应用，任一失败整体回滚 + 结构化诊断

## 视频抽帧评估闭环

- ffmpeg 抽帧（均匀 4 帧避黑场首尾）→ 逐帧 Tier0+VLM 区域级诊断 → 聚合判定（任一帧致命问题=不达标，附帧级诊断）
- `view_video` 工具支持 `use_last`；视频任务完成后大脑自动评估
- ffmpeg 路径：环境变量 `FFMPEG_PATH`/`FFPROBE_PATH`，默认 D 盘已装路径

## 通用执行引擎（大脑传工作流 → 引擎跑任意工作流）

所有生成统一走 `run_workflow(任意API格式工作流)` 五段管线，返回统一 WorkflowResult：

```
本地校验 ──失败──→ {stage: validation_failed, issues}      ← 结构化回流大脑
确定性自动修复(≤3轮) ──修复不完──→ {stage: repair_failed}
提交(服务器校验→参数修复→重试≤3) → 执行(OOM→降参+一次重试)
  ──仍失败──→ {stage: execution_failed, exec_error, suggestion}  ← 回流大脑
下载 → {stage: completed, outputs}
```

- 来源无关：模板渲染 / 图合成 / `load_workflow`（任意本地工作流文件→转换）都走同一条管线
- 失败回流大脑：stage=failed 的结构化诊断进 observation，大脑做**语义级修复**（edit_workflow/propose_edit/learn_node）后重跑

## Skill 信息库（全量实景化，渐进披露）

- **全量档案**：1527 个节点全部有中文语义档案（用途/输入接线/输出去向/典型接线/坑），批量生成+断点续跑（`scripts/build_node_skills.py`）
- **接地气**：档案依据 = 签名 + 本机模型枚举 + **本机 98 个工作流的真实用法片段**（90 blueprints + 用户工作流反向索引）
- **渐进披露**：L0 索引（类名→一行用途，1511/1527 覆盖）→ search_nodes（中文概念翻译+LLM精排）→ learn_node（完整档案）→ read_skill（家族方法论）
- **家族技能**：12 个功能家族文档（采样/潜空间/ControlNet/视频...），按需读取不进上下文
- **上下文纪律**：只注入轻量参数技能；建图方法论按需读——避免"从零建图优先"的偏见

## Web UI（可视化）

```bash
python -m brain --web   # 启动内置 Web UI，浏览器自动打开 http://127.0.0.1:8899
```

纯标准库 SSE 服务器 + 单文件前端（零依赖零构建），三栏布局：

- **左·对话流**：用户消息 / AI 思考流式（reasoning 折叠 + 正文双通道）/ 工具调用卡片（参数+成败+诊断）
- **中·工作流图**：手写 SVG 分层布局实时渲染 AI 搭建的工作流——节点按家族着色、边标输入名、失败节点红色、执行中呼吸灯、点击看签名
- **右·详情**：修复记录 / 评估卡片（分数+区域诊断）/ 技能召回 badge / 产物画廊
- **顶栏**：五段管线进度条 + GPU 显存仪表 + 队列位置
- **传图改图**：输入栏 📎 按钮（或拖拽图片到聊天区 / Ctrl+V 粘贴，单张，PNG/JPEG/WebP ≤25MB）——发送后图片自动落盘项目 uploads/ 并上传 ComfyUI /input，大脑先 analyze_image 看图再选 i2i/style_transfer 执行，用户消息气泡内显示缩略图（点击看大图）

架构：事件总线（brain/events.py，类型化事件）+ SSE 推送 + 会话文件审计——前端只订阅渲染不做逻辑，后端是唯一真相源。

## 已验证（真实执行）

- ✅ **大脑自然语言闭环**（本机实测）：`python -m brain "画一只戴宇航头盔的橘猫，星空背景"` → 大脑选模板、写提示词、执行、glm-4.6v 评估（第一张误生成"宇航员+猫"被 VLM 判 3 分）、按评估建议重试、第二张 9 分通过并交付
- ✅ **多模态闭环**：传图改吉卜力风 → analyze_image 先看图 → style_transfer 遇 OpenPose 模型缺失（HF 网络不可达）自动降级 i2i → 四轮参数迭代（denoise 0.7→0.85）→ 7/10 交付；轨迹沉淀为技能
- ✅ **技能跨进程召回**：重启大脑后同类任务 `[记忆] 召回 1 条相似任务经验`，首次即写出正确提示词
- ✅ **通用引擎**：任意工作流文件（用户图生图.json）→ load_workflow → VAE结构修复 → 执行出图；坏工作流 → 结构化 validation_failed → 大脑语义决策重建 → 成功
- ✅ **动漫画作全链路**：自然语言 → run_template → VLM评估 9 分交付（10个工具调用零干预）
- ✅ **5秒视频全链路**：minimax_t2v 真实出片（agent_minimax_00001_.mp4）→ view_video 抽帧评估 9 分交付
- ✅ **Skill 库**：1527 节点全量档案、1511 个 L0 一行用途、12 家族技能、抽查 20/20 档案有描述
- ✅ 图合成：compose(t2i→upscale_pass) 两段产物；ControlNet(canny) 锁构图真实出图
- ✅ 从零建图：scaffold→propose_edit→真实执行出图
- ✅ 跨设备适配：缺失模板默认模型时自动绑定本机同家族 checkpoint（friend-mode 仿真验证）
- ✅ 89/89 单测通过

## Phase 2 路线（见调研报告）

- 视频抽帧评估（接 ffmpeg）+ 本机 Qwen2-VL 快筛
- 人脸检测 Tier0（Impact Pack 工作流化）
- 受限图合成（验证引导逐边构建，超越模板库的组合需求）
- 技能库沉淀（成功轨迹→新模板）
