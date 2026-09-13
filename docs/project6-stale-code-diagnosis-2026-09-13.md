# 项目 6 工具反复失败的诊断与修复（2026-09-13）

用户问："为什么本次项目 6 的工作 LLM 调用工具的行为重复失败？"
结论：**主因是服务在跑旧代码**（我改完没重启 Web UI），次因是裸文件名解析不搜产物目录，
再加上一条**我自己引入的缺陷**——止损台账把"根本没跑起来的技术失败"也算成了"手法试过了"，
于是策略被锁死、模型只能反复撞墙。

## 一、事故链（`proj_e7f6c011`，10:33:50–10:38:37，10 次工具调用中 5 次失败）

| 时刻 | 事件 | 归因 |
|---|---|---|
| 10:34:06 | `run_template(i2i)` + `width/height` → `validation_failed`「模板 i2i 不支持参数 ['width','height']，已阻止执行」 | **新护栏按设计工作**（旧行为是静默忽略、交付里写错尺寸）。大脑随即改走 `t2i` 成功 |
| 10:36:42 | `local_repair(target=face)`，`image="agent_t2i_00062_.png"` → `repair_failed` | **主因**：服务旧代码里 `local_repair` 没有输入文件声明 → 引擎不上传 → 原样送进工作流 → ComfyUI `LoadImage.VALIDATE_INPUTS` 判不在 `/input` → `Invalid image file` |
| 10:36:53 | 同一调用改传绝对路径 → 同样 `repair_failed` | 同上；绝对路径还被 `is_within_directory` 判定越出 input 目录 |
| 10:37:04 | 第三次同手法 → `blocked=true`「该手法已执行 1 次…必须换手法」 | **我的缺陷**：前两次是技术失败，却被算作"手法已试 1 次" |
| 10:37:09 | `analyze_image("agent_t2i_00062_.png")` → 「图片不存在」 | 次因：`_resolve_upload` 只搜 uploads/，不搜产物目录（该文件是上一轮产物） |
| 10:37:35 | 第四次同手法 → `blocked=true`「已提醒 1 次仍未换手法：拒绝继续重掷」 | 同上缺陷的第二次发作 |

### 证据（决定性）
- 处理请求的引擎进程 **09:58:40 启动**（PID 12468），而 `INPUT_FILE_PARAMS` 里加 `local_repair`
  是 **10:08 之后**（`templates/base.py` 最后改动 10:10:47）。
- 该项目日志里**一次都没有**引擎的 `"<参数> 已自动上传到 ComfyUI /input"` 提示
  （同期的其它三个项目分别有 21/16/8 次）。
- `workflow_update` 事件里 LoadImage 的 `image` 正是两个**未上传的原始值**。
- ComfyUI 控制台日志（`.comfy-agent/logs/comfyui.log`）逐字记录了拒绝原因：
  `Custom validation failed for node: image - Invalid image file: agent_t2i_00062_.png`。
- 反证：用**当前代码**重放同一调用 → `stage=completed`，上传提示正常。
- 位置：`LoadImage.VALIDATE_INPUTS` → `folder_paths.exists_annotated_filepath()`（`os.path.exists` 语义，
  只查文件在不在 input 目录，不解析图像内容）。

## 二、修复

1. **引擎代码新鲜度自检**（`comfy_agent/freshness.py`，新）
   启动时对 17 个决策/执行模块取指纹（mtime_ns+size）；每回合开始比对磁盘：
   - 不一致 → 发 `stage warning`、写日志、在对话里注入"不要调用生成类工具"的指令；
   - 循环里**硬拦** `run_template`/`run_workflow`/`submit`（陈旧代码下不烧 GPU、不用旧逻辑出图）；
   - `/api/status` 暴露 `stale_code`，前端用**不自动消失的红色提示**显示"请重启服务"。
2. **裸文件名解析补齐产物目录**（`runner._ensure_inputs_uploaded` + `tools._resolve_upload`）
   依次搜：本轮上传 → 项目 uploads（精确名）→ **项目 outputs（含跨轮子目录，精确名）**；
   仍找不到时问一次服务器 `/input` 清单，若也不在 → 返回**可执行的报错**
   （"先用 upload_image 或传绝对路径"），而不是把原值丢给 LoadImage 让服务器以
   `Invalid image file` 拒收。仍然**只认精确匹配**，不恢复模糊替换。
3. **技术失败不消耗手法额度**（`brain/task.py`）
   `AttemptLedger.record(technical=...)`：`render_failed/validation_failed/repair_failed/
   execution_failed` 与"遮罩为空"只记 `tech_failures`，**不增加同手法次数**；
   `check()` 只看"真跑起来但没达标"的次数 → 配置性失败不再锁死策略，
   而"同一手法两次没修好"依旧会被拦。
4. **`bad_input` 失败类**（`brain/policy.py`）
   `Invalid image file` / `图片不存在` / 找不到输入文件 → 新类 `BAD_INPUT`，
   动作 `fix_input_path/upload_first/ask_user`，`may_rerender=True`（修好路径后重试同一步是正当的），
   `download_helps=False`（不是缺模型，别去下载）。归类顺序放在最前，避免被别的类抢走。
5. **空遮罩不许当"修复成功"**（`brain/agent.py`）
   遮罩覆盖率 0（没定位到目标）→ 记 `task.notes`、按技术失败计、并注入
   "遮罩区实际没有被重绘，不要当修复成功交付，请用户给黑白遮罩/坐标"。

## 三、验证

- 新增 `tests/test_stale_code_and_input.py`（22 项）：指纹比对（改动/删除检出、陈旧拒绝生成）、
  裸名从产物目录解析、未知名字给可执行报错、服务器名原样放行、模糊名**不**替换、
  技术失败不锁死手法（复刻项目 6 两次技术失败后第三次**应当允许**）、
  语义失败仍按 2 次止损、`bad_input` 归类与优先级、空遮罩不当成功。
- 全量 `python -m unittest discover -s tests` → **292 tests OK**。
- 真机复现项目 6 那句请求（新建一张"从未上传过"的产物，让大脑用裸名引用）：
  - 引擎自己找到并上传 → `stage=completed`、`technical=False`、**tech_failures 0**、
    无 `Invalid image file`、无幽灵 `blocked`；1 次渲染 22s、GPU 58°C。
  - 同时暴露并已修掉一个诚实性缺口：那次 face 路线的遮罩覆盖率是 **0.0000**
    （DWPose 检测不到动漫脸），"修复"其实什么都没改，而交付却说"评估 8/10 通过"——
    现在这种情况会被标为技术失败并要求大脑如实说明、请用户给遮罩。

## 四、仍未解决 / 边界
- **face 路线对动漫内容无效**：DWPose/YOLOX 是照片训练的（两张真实动漫产物覆盖率均 0.000%）。
  可用的替代：让用户给黑白遮罩，或装动漫专用脸检权重（未做）。
- 指纹自检要求"改代码必须重启服务"；这是刻意的——静默跑旧代码的危险远大于重启的麻烦。
- `stale_code` 只在**回合开始**判定，因此"运行中改代码"要到下一回合才被发现。
