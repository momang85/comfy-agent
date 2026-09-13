# 自动走局部修复（local_repair）：落地与实测（2026-09-13）

针对"局部问题只能整图重绘"（项目 5 里"要修手"连着重绘 3 次，分数 6→4→6→6），本轮把
**自动改走局部修复**做成了机制：引擎层路由 + 四条遮罩链 + 三条保命护栏。

## 一、机制

### 1) 新模板 `local_repair`（`comfy_agent/templates/image.py`）
`image` + `prompt` + `target`(`auto|hand|face|box|provided`) → 四条遮罩子链 → 统一尾巴：
`VAEEncodeForInpaint` → `KSampler` → `VAEDecodeTiled` → **`ImageCompositeMasked(destination=原图, mask=遮罩)`**
→ `SaveImage`，并另存一张**遮罩预览**（供引擎判定"是不是真的局部"）。
遮罩外像素因此逐像素保持原图（下方实测已证）。

### 2) 引擎自动路由（`brain/agent.py::_auto_local_repair`）
台账拦下"同手法重掷"时，满足**五个条件**就自动改跑 `local_repair`，而不是再渲染一张整图：
① 契约判定是局部修复（修手/修脸/那块/局部…）② 台账显示只试过整图 ③ 最近评估 `verdict=false` 或分数<7
④ 有基底图（上一版产物或本轮上传）⑤ 世界模型确认该路线的节点在本机存在。
目标推断：手/指→`hand`，脸/面/五官→`face`，**真的给了比例框**→`box`，都不匹配→如实请用户给遮罩（不猜）。

### 3) 三条保命护栏
- **遮罩绝不从本轮上传自动填充**：`INPUT_FILE_PARAMS["local_repair"]` 里 mask 的 kind 是 `mask`，
  引擎拒绝用"用户刚传的原图"充当遮罩（那等于整图重绘）。条件型输入由 `Template.needs_input()` 声明
  （mask 只在 `target=provided` 时需要）。
- **遮罩有效性判定**（`comfy_agent/mask.py`，纯标准库读 8-bit PNG）：覆盖率≈0 → **没检测到目标**，
  不许声称修好；覆盖率 >60% → 这不是局部修复，拦下并说明。
- **运行时依赖失败如实归因**：节点在但 python 依赖缺失（实测 `No module named 'ultralytics'`）
  → 按"节点/依赖问题"说明，明确告知"下模型没用"，并请用户给遮罩或换做法。

另外补了两个已发现的缺口：`strategy_signature` 把 `local_repair` 计入 `inpaint` 手法（台账的
"未做局部修复"与止损才成立）；`tool_run_workflow` 之前漏传 `criteria`（合成链路评估用的是通用兜底判据）。

## 二、真机实测（ComfyUI 0.33 / RTX 50 系 12GB，项目 5 真实产物 920×1128）

| 路线 | 结果 | 证据 |
|---|---|---|
| **box（矩形）** | ✅ 可用 | 遮罩 920×1128（与图同尺寸）、覆盖率 12.6%；**遮罩内 100% 被重绘，区域外 0 像素改动**（含 16px 软边余量）；17–25s；GPU ≤57°C |
| **provided（用户遮罩）** | ✅ 可用 | 同一区域、同一条尾巴，产物与 box 路线一致；25s |
| **hand（RMBG 手部 YOLO）** | ⚠️ **本机跑不起来** | `execution_failed`：`AILab_YoloV8Adv` → `No module named 'ultralytics'`。节点已注册、权重 `hand_yolov8s.pt`/`PitHandDetailer-v2-Test-v9c.pt` 都在盘上，**缺的是 python 依赖 `ultralytics`**（ComfyUI 解释器里没有；`cv2 4.13.0`/`onnxruntime 1.23.2`/`torch` 都在） |
| **face（DWPose 关键点）** | ❌ 对动漫图无效 | 链路能跑完（100s），但遮罩覆盖率 **0.000%**：两张真实项目产物（特写 + 全身）都检测不到人。DWPose/YOLOX 是**照片**训练的检测器，动漫插画上不работа；引擎已如实报"没检测到要修的目标" |

> 顺带解释了一个老问题：用户此前被建议"装 ComfyUI-Impact-Pack 就能用 ultralytics 检测器"——
> Impact 包**已经装了**，缺的是同一个 `ultralytics` 依赖（所以 `UltralyticsDetectorProvider` 没注册成功）。
> 装一次 `ultralytics` 会同时打通：RMBG 手部 YOLO（自动局部修复的 hand 路线）与 Impact 的检测器链路。

## 三、测试

- 新增 `tests/test_local_repair.py`（含 4 条路线渲染结构、"四条路线声明的节点都在本机快照里"、
  遮罩护栏、遮罩覆盖率判定、自动路由 5 条件矩阵、依赖失败归因、尺寸按真实图像）。
- 扩展 `tests/test_project5_replay.py`：**项目 5 那轮"要修手"现在会自动选 `local_repair(target=hand)`**，
  只渲染 1 次且不再算"未做局部修复"（当时是 3 次整图重绘）。
- 全量 `python -m unittest discover -s tests` → **268 tests OK**。

## 四、没做/未解决（诚实登记）

1. **hand 路线需要装 `ultralytics`**（一条 pip 命令，约 50MB，torch 已有）——会改动用户的 ComfyUI 环境，
   未擅自安装；装完 hand 路线即自动可用（PitHandDetailer 模型本身就是为 AI 绘画的手部修的，命中率比通用 YOLO 高）。
2. **face 路线对动漫内容无效**：需要换检测器（如动漫专用脸检权重，或用户给的遮罩）。已如实回落到"请用户给遮罩"。
3. `provided` 路线的遮罩若与原图尺寸不同，ComfyUI 会缩放，区域可能轻微偏移（box 路线已按真实尺寸修好）。
4. 自动路由只在"台账拦下重掷"那一刻触发；主动说"帮我修手"时仍由大脑选模板（repair.md 已把 local_repair 列为首选）。
