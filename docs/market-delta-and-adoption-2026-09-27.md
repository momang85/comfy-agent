# 市场增量与采用策略（2026-09-27）

**定位**：在 `ComfyUI全能Agent-市场调研与可行性报告.md`（2026-09-07）基础上，补最近 20 天的市场变化、审查 comfy-agent 当前状态，回答一个问题：**怎么改才能让更多人用**。

**结论速览**：工程底座已经够好，卡住采用的不是功能，是**门面、渠道、语言、key 门槛**四件事。官方 Comfy Agent 已进入 in-app 私测、官方 MCP 已 GA、第三方通用面板开始退场——"通用入口"窗口比 9-7 报告判断的更窄；但官方不覆盖的三个面（BYOK/本地 LLM、深度执行-修复闭环、中文垂直场景）正是本项目已有能力，收缩定位 + 补齐分发即可获得第一批真实用户。

---

## 1. 市场增量（2026-09-07 → 09-27）

### 1.1 官方动作（最重要的变化）

- **Comfy Agent 进入应用内私测**（docs.comfy.org/agent-tools/in-app-agent）：ComfyUI 界面内聊天，能找模板/节点/模型、从模板建工作流并改提示词、对现有工作流做局部编辑、增删连线节点、运行前校验、跑工作流返回产物、媒体编辑（剪/拼/加音轨）。**用 Comfy 服务端的模型，用户不需要配任何 LLM key**；运行权限分 Ask/Auto 两档。状态是 rolling out to limited users + waitlist，无 GA 日期。
- **Comfy MCP 宣布 GA**（2026-06-30 官方博客 "Turn your agent into a creative technologist"）；本地版仓库 237 stars，活跃。

### 1.2 第三方格局变动

| 项目 | 9-07 stars | 9-27 stars | 变化与信号 |
|---|---|---|---|
| Comfy-Org/ComfyUI | 131,823 | 135,176 | 主生态持续增长 |
| Comfy-Org/comfy-mcp（官方） | 205 | 237 | 稳定爬升 |
| artokun/comfyui-mcp | 729 | 765 | 20 天 +36，"MCP server + 侧边栏"路线有人买单 |
| artokun/comfyui-mcp-panel | — | 120 | **README 明确停止维护**："ComfyUI now ships official agent and MCP tooling" |
| ATH-MaaS/ComfyUI-Copilot | 5,504 | 5,528 | 增长近乎停滞，托管 API 停摆的处境未变 |
| SlavaSexton/ComfyUI-Agent-Kit | — | 102 | 新物种：一个月破百星，"给每个编码 agent 的 ComfyUI 技能" |
| heshengtao/comfyui_LLM_party | 2,349 | 2,370 | push 停在 07-29，实质停滞 |
| **momang85/comfy-agent（本项目）** | — | **1** | 仓库公开（09-19 首推），**自然流量为零** |

三个信号：
1. **"通用小白入口"赛道基本被官方收编**——9-7 报告"别做通用入口"的判断被强化，窗口进一步收窄。
2. **MCP/技能分发路线跑通了**：artokun 靠 MCP 路线保持增速，Agent-Kit 一个月 102 星证明"把 ComfyUI 变成编码 agent 的工具"有现成受众。
3. **厂商 agent 开始把 ComfyUI 当执行后端**：MiniMax Design 支持连接本地 ComfyUI 工作流并由 agent 调节点参数。

### 1.3 对本项目定位的含义

官方 Agent 覆盖的是"画布内搭工作流 + 跑"，且绑定 Comfy 官方服务。它**没有覆盖**、短期也未必覆盖的：

1. **BYOK / 本地 LLM**：隐私敏感用户、已有 z.ai/DeepSeek/Kimi/Ollama 的用户、不想被 Comfy 云服务锁定的用户；
2. **执行-修复-交付的深度闭环**：VLM 区域级评估 → 参数级/结构级修复 → 局部修复只动遮罩区 → 缺模型搜索+确认下载 → GPU 温度护栏。官方目前公开信息里是"搭+跑+校验"，没有"看结果不满意自动修"的同深度闭环；
3. **中文市场与绘世启动器生态**：官方英文优先；国内秋叶整合包/绘世启动器用户基数大但无人服务。

一句话定位修正：**官方 Agent 管画布内"搭"，comfy-agent 管画布外"跑、修、交付"**——本机 ComfyUI 的 BYOK 聊天执行引擎。

---

## 2. 现状审查

### 2.1 工程底座（结论：够好，不需要为采用而重构）

- 核心代码约 10,200 行（comfy_agent/ + brain/，不含模板与测试），**296 项单测 16 秒全绿**，MIT 协议，零第三方依赖（纯标准库，ComfyUI 整合包自带 Python 可跑）。
- 9-7 报告的 Phase 1 清单**全部达成**（模板库+参数化、验证闭环、多模态输入、≤3 次修复）；Phase 2 大半落地（VLM 区域级评估、技能沉淀、受限图合成、视频抽帧评估、局部修复四条遮罩链、缺模型搜索下载、代码新鲜度自检）。
- 安全设计完整：SSRF 防护、路径限制、下载源校验、key 本地化。Mimosa 深度扫描 0 发现（scan-2026-09-27T15-51-24，sha256:0890…73d15fe）。

### 2.2 采用侧硬伤（按影响排序）

| # | 问题 | 证据 | 影响 |
|---|---|---|---|
| 1 | **门面为零** | README 292 行全中文长文，无一句话价值主张、无 demo GIF/截图；仓库 1 star | 新手前 10 秒不知道"这是什么、为什么值得装"；搜索引擎与 GitHub topics 流量为零 |
| 2 | **不在 Comfy Registry/Manager** | 未打包成 custom node | 放弃了 ComfyUI 生态最大分发渠道（Copilot 5500+ 星的主要来源就是 Manager 一键装） |
| 3 | **纯中文** | UI 与文档无英文 | 直接排除英文社区——那是 ComfyUI 生态最大盘子 |
| 4 | **LLM key 前置** | 三步安装第 3 步就要 key；Ollama 支持埋在 FAQ；智谱 GLM-Flash 免费档未写进快速开始 | 没有零成本体验路径，转化漏斗在第一步就漏 |
| 5 | **平台体验不均** | Windows 有 install.bat/一键启动.bat；mac/linux 只有一行环境变量说明 | 英文社区 mac 用户占比高，一行带过等于劝退 |
| 6 | 机器残留 | brain/eval/video.py:26 ffmpeg 兜底路径写死本机 D 盘（env→PATH 之后才轮到，且有可操作报错，属小瑕疵）；README 写"89/89 单测、11 个单测文件"（实际 296/18） | 可信度小折扣 |
| 7 | 流程卫生 | 本轮发现上轮 265 行修复游离在暂存区（内容已随 a068fbf 在远端，本地已对齐） | 说明"改完没提交"会静默积累 |

---

## 3. 怎么改才能让更多人用（按投入产出排序）

### P0：门面（1–2 天，纯增量不动代码逻辑）

1. **README 重构**：顶部三行 = 一句话定位（"Your local ComfyUI, driven by chat. BYOK, repair-loop, 中文友好"）+ demo GIF + 三步安装；现有长文拆进 docs/。中英双语（英文版完整、中文版保留）。
2. **录一张 30 秒 GIF**：浏览器实跑"传图 → 改吉卜力风 → 自动评估迭代 → 交付"，直接放 README 顶部。
3. **GitHub topics**（comfyui / agent / stable-diffusion / llm / sdxl / chinese）+ About 一句话 + 截图区。

### P0：渠道（2–4 天）

4. **打包成 ComfyUI custom node 提交 Comfy Registry**：做一个薄的"comfy-agent bridge"节点包（Manager 一键装、装完带启动菜单/按钮拉起 Web UI）。定位写清楚"画布外的执行引擎"，与官方 Agent 互补而非竞争，过审风险低。这是唯一能借到 Manager 装机量的通道。
5. **发布帖子**：英文 r/comfyui + GitHub Trending 时间卡位；中文 Bilibili 教程 + 绘世启动器社区（目标用户已在那）。

### P1：门槛（一周内）

6. **零 key 快速路径**：快速开始第一条改成"5 分钟零成本跑通"= ComfyUI + Ollama（本地，无 key）或智谱 GLM-Flash（免费档）的具体组合；现三步里的 key 一步降级为"进阶"。
7. **UI i18n**：app.js 是单文件，抽一个中英字符串 dict 成本低；引擎诊断文案可后置。
8. **mac/linux 脚本平权**：install.sh + start.sh 对齐 bat 能力，README 平台并列展示。
9. **清残留**：ffmpeg 兜底路径改为"PATH → ComfyUI 整合包常见位置"；README 数字校准。

### P1：产品钩子（决定留存与传播）

10. **垂直模板包**：电商换背景/换模特、IP 角色一致性套图（9-7 报告 Phase 3 首选场景）做成"一键装模板"，既是差异化也是教程视频的标题素材。

### P2：骑 MCP 生态的势（可选，性价比高）

11. 把执行引擎包一层 **MCP server**（对标 artokun 765 星路线）或 Claude Code/Codex 技能（对标 Agent-Kit 一个月 102 星）：让 Claude Desktop/Cursor 用户把"本机 ComfyUI 的执行+修复闭环"当工具用——零边际营销成本，且与 Web UI 共用同一引擎。

### 不做清单

- 不做"通用小白入口"与官方 Agent 正面竞争（免费 + 内置 + 服务端模型，正面战场没有胜率）；
- 不做托管服务/代跑（Copilot 托管 API 停摆前车之鉴；BYOK 本机路线成本为零）。

---

## 4. 如果只做三件事

1. **README + demo GIF + 英文版**（P0 门面）——把已有工程价值翻译成 10 秒可感知的价值；
2. **Registry/Manager 上架**（P0 渠道）——到用户聚集的地方去；
3. **零 key 快速路径**（P1 门槛）——让人免费跑通第一次，第一次的成功体验就是传播本身。
