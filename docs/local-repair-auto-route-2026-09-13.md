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
| **hand（RMBG 手部 YOLO）** | ✅ **可用（装依赖后）** | 装 `ultralytics` 前：`execution_failed: No module named 'ultralytics'`（节点与权重都在，缺 python 依赖）。装完（`pip install --no-deps ultralytics ultralytics-thop ultralytics-platform`，**刻意不加 opencv-python** 以免替换现有 cv2 4.13.0）后：`hand_yolov8s.pt` 遮罩覆盖率 **1.20%**（bbox 631,491–757,592）、`PitHandDetailer-v2-Test-v9c.pt` **0.71%**，两者**区域外改动均 0 px**，40–123s，GPU ≤58°C |
| **face（旧 DWPose）** | ❌ 已弃用 | DWPose/YOLOX 是照片训练的检测器，两张真实动漫产物掩码覆盖率都是 **0.000%**；`UltralyticsDetectorProvider` 又因缺 Impact-Subpack 未注册 |
| **face（新：YOLO 分割）** | ✅ 可用 | 换成 `AILab_YoloV8Adv` + 脸部**分割**权重（都放在 `models/ultralytics/`，免重启即被枚举）：<br>· **动漫专用** `anime_face_seg_v3_y11n.pt`（Anzhc Face seg 640 v3，YOLO11n-seg，单类 face，插画掩码 mAP50 0.871，AGPL-3.0，5.80MB）——实测在**全身图**上找到 0.69% 的小脸紧框（照片向权重在同样图上 0.000%），修复后区域外 **0 像素**改动、评估通过 ✅<br>· 兜底 `face_yolov8n-seg2_60.pt`（Manager 目录里的 face/hair/skin 分割，6.77MB）——能找到脸但区域偏松（含头发），质量一般<br>· `conf=0.10`（实测 0.25 检测不到动漫脸） |
| **denoise 灰块（新发现）** | ✅ FIXED | 普通 SDXL 不是 inpaint 模型，`VAEEncodeForInpaint` 用灰填充遮罩区：实测 **0.65 → 平灰块、0.80 → 深灰块带残线、0.85 → 正常出图**。引擎现在对 `local_repair` 硬收敛到 ≥0.85（`runner._guard_params`），大脑自己传 0.45 也拦得住 |
| **判据丢失（新发现）** | ✅ FIXED | "脸部和手有一些失真"这类**报缺陷**说法原本不匹配任何约束 → 评估退回通用套话（给过无关的 8/10）。现在 脸部/失真/糊块 等都进判据 |

> 顺带解释了一个老问题：用户此前被建议"装 ComfyUI-Impact-Pack 就能用 ultralytics 检测器"——
> Impact 包**已经装了**，缺的是同一个 `ultralytics` 依赖（所以 `UltralyticsDetectorProvider` 没注册成功）。
> 装一次 `ultralytics` 会同时打通：RMBG 手部 YOLO（自动局部修复的 hand 路线）与 Impact 的检测器链路。

## 三、测试

- 新增 `tests/test_local_repair.py`（含 4 条路线渲染结构、"四条路线声明的节点都在本机快照里"、
  遮罩护栏、遮罩覆盖率判定、自动路由 5 条件矩阵、依赖失败归因、尺寸按真实图像）。
- 扩展 `tests/test_project5_replay.py`：**项目 5 那轮"要修手"现在会自动选 `local_repair(target=hand)`**，
  只渲染 1 次且不再算"未做局部修复"（当时是 3 次整图重绘）。
- 全量 `python -m unittest discover -s tests` → **270 tests OK**（新增口语说法识别：实测漏过"修一下手"）。

## 三·B 端到端自动路由实测（真机，2026-09-13）

脚本化 LLM 跑真实一轮（同一张动漫图，需求"手崩了，帮我修一下手；另外这张不是全身照，要全身"）：

| 步骤 | 结果 |
|---|---|
| ① 整图 i2i | 完成，评估 **4/10 未通过**（判据含"必须全身"） |
| ② 第 2 次同手法重掷 | **被台账拦下**（只改 denoise/seed 无效）——日志：`[自动局部修复] target=hand base=…agent_i2i_00024_.png` |
| ③ 引擎自动改走局部修复 | 自动执行 `local_repair(target=hand)`，基底=上一版产物 |
| ④ 收尾 | 技法 `['global','inpaint']`；**共 2 次渲染、拦截浪费 0**；交付如实写"评估：未通过（2/10）对照你的要求：必须全身…" |

对照项目 5 同场景：当时是 3 次整图重绘、分数 6→4→6→6、交付谎称"要求已达成"。现在：2 次渲染（1 次整图 +
1 次真正的局部修复）、不再有"同手法重掷"、结论如实。

## 四、没做/未解决（诚实登记）

1. ~~hand 路线需要装 `ultralytics`~~ → **已装**（用户批准）：`pip install --no-deps ultralytics
   ultralytics-thop ultralytics-platform`，**cv2 保持 4.13.0 未被替换**；hand 路线实测可用。
   注：Impact 的 `UltralyticsDetectorProvider` 重启后**仍未注册**（还有别的注册条件），但自动局部修复
   走的是 RMBG 的 `AILab_YoloV8Adv`，不受影响。
2. **face 路线：机制通了，质量还不够**。已把 DWPose（0.000%）换成目录里的 `face_yolov8n-seg2_60.pt`，
   掩码从"空"变成 19.7%–24.7% 的局部区域、区域外 0 像素改动，但该权重**是照片向的**，
   在动漫图上把头发+大半张脸一起圈进去 → 重绘结果不自然（"紫脸/灰脸"）。
   **建议下一步装动漫专用分割权重**（Anzhc Face seg 640 v3 y11n.pt，6.1MB，YOLO11n-seg，
   单类 face，插画掩码 mAP50 0.871）+ 可选 `Anzhc HeadHair seg`；装法与本次相同
   （丢进 `models/ultralytics/` 即可，免重启）。许可提醒：Anzhc 与 ultralytics 均为 AGPL-3.0。
3. `provided` 路线的遮罩若与原图尺寸不同，ComfyUI 会缩放，区域可能轻微偏移（box 路线已按真实尺寸修好）。
4. 自动路由只在"台账拦下重掷"那一刻触发；主动说"帮我修手"时仍由大脑选模板（repair.md 已把 local_repair 列为首选）。
