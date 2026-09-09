# -*- coding: utf-8 -*-
"""大脑主循环：think -> act -> observe（含计划模式与成本意识）。

与通用 agent 的差异（领域定制）：
- 每轮系统提示注入：模板目录 + 本机模型摘要 + 提示词规范
- GPU 成本意识：执行前强制走本地校验；视频任务提示时长；批量超限拦截
- 计划模式：多步/视频任务先出方案征询确认，再花 GPU 时间
- 交付物：过程说明 + 产物路径 + 评估结论
"""
from __future__ import annotations

import json
import re

from .llm import LLMClient, LLMError
from .tools import ToolContext, execute_tool, tools_schema_for_llm
from .memory import SkillStore

MAX_TURNS = 30


class Brain:
    def __init__(self, ask_user_fn=None, verbose=True, project=None):
        from .projects import ProjectStore
        self.project = project or ProjectStore().ensure_default()
        self.llm = LLMClient()
        self.ctx = ToolContext(ask_user_fn=ask_user_fn,
                               project=self.project)
        self.verbose = verbose
        self.history: list[dict] = []     # LLM messages
        self.skills = SkillStore(self.project.skills_path())
        self._run_signatures: dict = {}  # 相同参数重试护栏（(模板,参数) → 次数）
        # 会话审计按项目落盘（事件按项目标签过滤）
        from .events import SessionAdapter
        self._session_log = SessionAdapter(self.project.history_path(),
                                           project_filter=self.project.id)
        self._init_system_prompt()

    # ---------- 主入口 ----------
    def handle(self, user_message: str, image: dict | None = None) -> str:
        """处理一条用户消息，返回给用户的回复文本。

        image: Web 端上传的图片信息 {url, name, local_path, server_name}，
        以系统上下文消息注入（先 analyze_image 看图再选图生图模板）。
        """
        from .events import emit as ev
        img_ev = None
        if image:
            img_ev = {"url": image.get("url"),
                      "name": image.get("name"),
                      "server_name": image.get("server_name")}
        ev("user_message", {"text": user_message, "image": img_ev})
        # 召回相似任务的历史经验（作为记忆注入，不占 system prompt）
        memory = self.skills.format_for_llm(user_message)
        self.history.append({"role": "user", "content": user_message})
        if image:
            note = "" if user_message.strip() else \
                "用户只上传了图片、未附文字说明——先 analyze_image 描述图片内容，" \
                "再向用户询问想怎么改。\n"
            self.history.append({"role": "user", "content":
                "[系统] 用户上传了一张图片：\n" + note +
                f"- 本地路径（analyze_image 用）：{image.get('local_path', '')}\n"
                f"- ComfyUI /input 文件名（模板 image 参数直接填这个）："
                f"{image.get('server_name', '')}\n"
                "改图时先调用 analyze_image(本地路径) 看懂图片内容/风格/构图，"
                "再选 i2i（保持构图改内容/风格）或 style_transfer（锁姿势线条换画风），"
                "模板的 image 参数填 server_name，不要重新上传。"})
        if memory:
            if self.verbose:
                self._log(f"[记忆] 召回 {memory.count('任务「')} 条相似任务经验")
            ev("memory_recalled", {"text": memory[:400]})
            self.history.append({"role": "user", "content": memory})

        final_reply = ""
        run_count = 0        # 确定性护栏：执行类工具调用次数
        for turn in range(MAX_TURNS):
            reply = self._chat_streamed()
            self.history.append({"role": "assistant", "content": reply})

            # 提取工具调用（```json {"tool":...}``` 或裸 JSON）
            call = _parse_tool_call(reply)
            if call is None:
                # 没有工具调用 = 大脑认为可以交付了
                final_reply = reply
                break
            name, args = call
            if name in ("run_template", "run_workflow", "submit"):
                run_count += 1
            # 护栏：相同参数重复提交（无效循环）——第2次即强提示改变策略
            if name == "run_template" and isinstance(args, dict):
                tid = args.get("template_id")
                params = json.dumps(args.get("params") or {}, sort_keys=True,
                                    ensure_ascii=False)
                sig = (tid, params)
                seen = self._run_signatures.get(sig, 0)
                self._run_signatures[sig] = seen + 1
                if seen >= 1:
                    self.history.append({"role": "user", "content":
                        "[system] 该 run_template 参数组合已执行过且未能解决问题。"
                        "禁止原样重试：必须改变策略（换节点/换参数/换工具/"
                        "search_nodes 找新方案）或 ask_user 询问用户。"})
            # 护栏：超过 4 次执行（首次+3次修复）强制交付，避免无限烧GPU
            if run_count > 4:
                self.history.append({"role": "user", "content":
                    "[system] 已执行 %d 次生成（首次+3次修复），达到硬性上限。"
                    "不要再调用执行类工具，基于已有结果完成交付总结。" % run_count})
                continue
            if self.verbose:
                self._log(f"[工具] {name} {json.dumps(args, ensure_ascii=False)[:120]}")
            ev("tool_start", {"tool": name, "args": args})
            draft_before = json.dumps(self.ctx.draft, default=str) \
                if self.ctx.draft else None
            result = execute_tool(self.ctx, name, args)
            # 警告（如幻觉参数被忽略）前置，确保截断窗口内可见
            head = ""
            if isinstance(result, dict) and result.get("warnings"):
                head = "⚠警告: " + "; ".join(str(w) for w in result["warnings"]) + "\n"
            obs = head + json.dumps(result, ensure_ascii=False, default=str)[:1200]
            self.history.append({"role": "user",
                                 "content": f"[observation] {obs}"})
            ev("tool_end", {"tool": name, "ok": bool(result.get("ok", True)),
                            "summary": obs[:500]})
            if self.verbose and not result.get("ok", True):
                self._log(f"  ↳ {obs[:160]}")
            # 草稿变更 → 推送工作流图
            if self.ctx.draft is not None:
                draft_now = json.dumps(self.ctx.draft, default=str)
                if draft_now != draft_before:
                    ev("workflow_update", {
                        "graph": self.ctx.draft,
                        "meta": self.ctx.draft_meta})
            self._compact_history()

        # 任务闭环：本轮真的生成了产物才沉淀技能（失败经验也记录）
        self._auto_remember(user_message)
        ev("delivery", {"text": final_reply or "（达到最大轮数，请继续提出要求）",
                        "outputs": self.ctx.draft_meta.get("last_outputs", [])})
        return final_reply or "（达到最大轮数，请继续提出要求）"

    # ---------- 内部 ----------
    def _chat_streamed(self) -> str:
        """流式对话：逐块 emit think_delta，返回完整文本。

        主循环关闭思考模式（thinking=False）：glm 思考模型的工具调用意图
        会随机分流到 reasoning/content 两通道，而本循环只解析 content——
        开启思考会导致调用丢失与预算被 reasoning 挤占（实测两种故障模式）。
        行动决策不需要推理链，结构化任务关思考已验证质量良好。"""
        from .events import emit as ev
        parts = []
        try:
            for channel, delta in self.llm.chat_stream(
                    self.history, temperature=0.4, thinking=False):
                if channel == "content":
                    parts.append(delta)
                ev("think_delta", {"channel": channel, "delta": delta})
        except LLMError:
            # 流式整体失败 → 回退非流式（同样关闭思考）
            reply = self.llm.chat(self.history, temperature=0.4,
                                  thinking=False)
            ev("think_delta", {"channel": "content", "delta": reply})
            return reply
        return "".join(parts)

    # ---------- 内部 ----------
    def _auto_remember(self, task: str):
        """任务结束时把轨迹沉淀为技能。

        成败判定：最后一条 VLM 评估结论优先（False=失败技能，召回时以
        "踩坑警告"呈现，防止失败方案被当作经验复用）；无评估时按执行结果。
        """
        traj = self.ctx.trajectory
        if not traj:
            return
        runs = [t for t in traj if t.get("action") == "run"]
        if not runs:
            return
        evals = [t for t in traj if t.get("action") == "eval"]
        verdict = evals[-1].get("pass") if evals else None

        if verdict is False:
            result = "失败"
        elif verdict is True:
            result = "成功"
        else:
            # 无评估：执行成功且不折腾（≤2次）才算成功，折腾过的存"待确认"
            result = "成功" if runs[-1].get("ok") and len(runs) <= 2 else "待确认"

        # 模板字段：管线优先，否则取首次成功执行的模板
        pipeline = self.ctx.draft_meta.get("pipeline")
        if pipeline:
            template = "->".join(pipeline)
        else:
            first_ok = next((t for t in runs if t.get("ok")), None)
            template = (first_ok or runs[-1]).get("template", "")

        notes = []
        for t in traj:
            if t.get("action") == "run" and not t.get("ok"):
                notes.append(t.get("note", "")[:160])
        if verdict is False and evals:
            notes.append("评估未达标: " + "；".join(evals[-1].get("issues", [])[:3]))

        from .events import emit as ev
        ev("skill_remembered", {"task": task[:80], "result": result})
        self.skills.remember(
            task,
            template=template,
            params=self.ctx.draft_meta.get("params", {}),
            prompt=self.ctx.draft_meta.get("params", {}).get("prompt", ""),
            result=result,
            trajectory=[{"action": t["action"], "note": t.get("note", "")[:160]}
                        for t in traj])

    # ---------- 内部 ----------
    def _init_system_prompt(self):
        from pathlib import Path
        from comfy_agent.templates import catalog
        from comfy_agent.promptspec import FAMILY_GUIDES
        tpl_lines = "\n".join(
            f"- {t['id']}: {t['name']}（{t['desc']}）显存~{t['vram_gb']}GB"
            f" 参数: [{', '.join(p['name'] for p in t['params'])}]"
            for t in catalog())
        prompt_guide = "\n".join(
            f"- {fam}: {g['lang']}；{g['style']}"
            for fam, g in FAMILY_GUIDES.items())
        # 领域技能库：仅注入轻量参数类技能；建图方法论（core-nodes/
        # workflow-design）按需 read_skill 读，避免"从零建图优先"的偏见
        skills_dir = Path(__file__).parent / "skills"
        always_inject = ("t2i.md", "video.md", "repair.md")
        skill_docs = []
        for f in sorted(skills_dir.glob("*.md")):
            if f.name in always_inject:
                skill_docs.append(f.read_text(encoding="utf-8").strip())
        skills_blob = "\n\n---\n\n".join(skill_docs)
        tool_call_example = '{"tool": "工具名", "args": {}}'
        self.history = [{"role": "system", "content": f"""你是 ComfyUI 生成任务的大脑，帮用户完成图像/视频生成。目标：把用户的自然语言变成高质量的生成结果。

## 可用模板
{tpl_lines}

## 任务→工具映射（严格遵守，先匹配再动手；绝大多数请求是单步生成！）
- **单步生成（"画X"、"生成X"、"生成一张XX风格的图"）→ 直接 run_template(t2i/i2i/style_transfer...)。这是最高频路径，不要为了简单任务去 scaffold/synthesize**
- **多步管线（"先生成再放大"、"画X然后放大"、"生成后加XX步骤"）→ 必须用 compose**：
  steps=[{{"template_id":"t2i","params":{{...}}}}, {{"template_id":"upscale_pass","params":{{"prompt":"画面内容简述"}}}}]
  禁止顺序调用两次 run_template 代替管线
- 模板上加节点（"加LoRA"、"改结构"）→ synthesize + propose_edit
- **锁构图/锁姿势的转绘（"用XX图做ControlNet引导"、"保持构图换内容"）→ style_transfer 模板，control_type=canny（离线可用）；只有模板做不到的结构变化才用 synthesize**
- 传图修改（"把这张图改成X风格"）→ analyze_image 先看图 → i2i/style_transfer
- **修复崩坏（"修复XX/修脸/手崩了/局部坏了"）→ 严格按 repair.md 配方：analyze_image 定位 → search_nodes 找 FaceDetailer/局部重绘节点 → 局部修复链。禁止全图 i2i 修局部**
- **自由合成（用户明说"自己搭工作流/自由合成/从零建/试试新节点"）→ read_skill(workflow-design/core-nodes) 读方法论 → scaffold 骨架 → 逐环节 propose_edit → 不认识的节点先 learn_node**
- **视频任务完成 → view_video 评估（use_last: true）**
- **加载现有工作流文件 → load_workflow(path) → 修 blocker → run_workflow**

## 执行协议（所有生成统一走 run_workflow，stage 结果驱动下一步）
- 返回 stage=completed：任务成功，进入评估/交付
- 返回 stage=validation_failed/repair_failed：读 validation_issues/unfixable 字段，
  用 edit_workflow/propose_edit 做语义修复（确定性规则已耗尽才轮到你，别重复无意义的原样重试）→ run_workflow 重跑
- 返回 stage=execution_failed：读 exec_error/suggestion（OOM 时自主降分辨率/批数），修复后重跑
- **硬性步骤：任何视频类任务（模板含 minimax/ltx/视频二字，或产物是视频）执行成功后，必须调用 view_video(use_last=true) 评估后才能交付——这是强制要求，与上面三条同级，不允许跳过**

## 提示词规范（按模板家族）
{prompt_guide}

## 工具（用 ```json 代码块调用，格式 {tool_call_example}；一次只调一个）
{tools_schema_for_llm()}

## 工作规则
1. **先理解再动手**：需求含糊（如"画张图"没说画什么）时用 ask_user 澄清；信息足够就直接执行。
2. **先看再干**：任务涉及图片（改图/风格转换/参考某张图）时，必须先 analyze_image 看懂图片（内容/风格/构图），再选模板写提示词——不要盲猜图片内容。
3. **提示词由你写**：把用户的中文需求翻译成符合家族规范的提示词（SDXL/SD1.5 用英文标签，MiniMax 可中文自然语言）。用户没提负面词就用家族默认。
4. **成本意识（GPU 时间是真金白银）**：提交前必须经过本地校验；视频任务先告知预计耗时再执行；批量>4张先告知。
5. **评估闭环**：图像任务完成后用 view_image 评估（criteria 写用户的核心要求；批量>3张时传 sample=3 抽检）。注意 view_image 的 ok 只表示评估动作成功，**评估结论看 pass_overall**（false=不达标）。不达标时按 issues[].fix_hint 修改参数重试（最多一次，不要无限循环）。
6. **增量迭代**：用户反馈"改XX"时用 edit_workflow（class_type 定位节点，如提示词节点是 CLIPTextEncode）修改上一版，不要从头重建。
7. **交付格式**：完成时用中文总结：做了什么（模板/关键参数）→ 产物在哪（本地路径）→ 评估结论。不再调用工具时输出纯文本即结束。
8. 模型/节点不确定时用 list_models / search_nodes / learn_node 查询，不要猜。
9. **模型自动适配**：图像模板（t2i/i2i/style_transfer/upscale_pass）的 checkpoint 会自动绑定本机模型——除非用户点名用某模型，否则不要传 ckpt 参数；技能文档里出现具体模型名只是开发机示例，本机缺失时引擎会自动换成同家族模型（settings.json 的 model_prefs 可指定偏好）。

## 领域技能库（节点速查 + 建图方法论，自由合成必读）
{skills_blob}"""}]

    def _log(self, msg: str):
        print(msg, flush=True)

    def _compact_history(self, keep_recent: int = 16):
        """上下文压缩：历史超过 system+keep_recent 时，丢弃最旧的中间消息，
        插入一条摘要占位（轻量模型长上下文会退化，必须裁剪）。"""
        if len(self.history) <= 1 + keep_recent + 2:
            return
        system = self.history[:1]
        recent = self.history[-(keep_recent):]
        self.history = system + [{
            "role": "user",
            "content": "[system] 中间历史已压缩。请基于最近的对话继续，"
                       "不要重复已完成的步骤。"
        }] + recent


def _parse_tool_call(reply: str):
    """提取工具调用。兼容三种形式：
    1. ```json {"tool": ...}``` 代码围栏（标准）
    2. 无围栏的 {"tool"/"task": ...} JSON 行（模型格式漂移）
    3. 裸 "工具名(args)" 文本（兜底，无参数）
    """
    # 1) 围栏形式
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", reply, re.S)
    if m:
        try:
            data = json.loads(m.group(1))
            name = _tool_name(data)
            if name:
                return name, data.get("args", {})
        except json.JSONDecodeError:
            pass
    # 2) 无围栏：逐行尝试解析含 tool/task 键的 JSON
    for line in reply.splitlines():
        line = line.strip()
        if not line.startswith("{") or '"tool"' not in line and '"task"' not in line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = _tool_name(data)
        if name:
            return name, data.get("args", {})
    # 3) 文本兜底：tool_name 且无参数的工具
    _init_tool_names()
    for tname in TOOL_NAME_INDEX:
        if re.search(rf"\b{tname}\b", reply):
            return tname, {}
    return None


def _tool_name(data: dict):
    for key in ("tool", "task", "action"):
        if isinstance(data.get(key), str):
            return data[key]
    return None


TOOL_NAME_INDEX = None   # 惰性填充，见 _init_tool_names()


def _init_tool_names():
    global TOOL_NAME_INDEX
    if TOOL_NAME_INDEX is None:
        from .tools import TOOLS
        TOOL_NAME_INDEX = list(TOOLS.keys())
