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
import sys
import time

from .llm import LLMClient, LLMError
from .tools import ToolContext, execute_tool, tools_schema_for_llm
from .memory import SkillStore

MAX_TURNS = 30


def _strip_think_blocks(text: str) -> str:
    """剥掉模型混进正文的 <think>…</think> 思考块。

    火山 Ark 会忽略 thinking 参数，把推理连同答案一起放进 content；
    不剥掉的话，用户会看到模型的内心独白，工具解析也可能被污染。
    """
    import re as _re
    cleaned = _re.sub(r"<think>.*?</think>", "", str(text or ""),
                      flags=_re.S)
    return cleaned.strip()


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
        # 引擎代码指纹：启动时快照，每回合比对（改完没重启 → 显式告警而非静默跑旧码）
        from comfy_agent.freshness import code_fingerprint
        self._code_fp = code_fingerprint()
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
            # 绑定为"本轮上传"：analyze_image / 图片参数默认用它，防止大脑拿
            # 项目历史产物顶替刚上传的图（实测发生过：看图 400 后它宣称
            # "根据历史记录这张图与之前那张相同"，用旧图跑了 t2i）
            self.ctx.current_upload = {
                "local_path": image.get("local_path", ""),
                "server_name": image.get("server_name", ""),
                "name": image.get("name", ""),
                "url": image.get("url", ""),
                "ts": time.time(),
            }
            note = "" if user_message.strip() else \
                "用户只上传了图片、未附文字说明——先 analyze_image 描述图片内容，" \
                "再向用户询问想怎么改。\n"
            self.history.append({"role": "user", "content":
                "[系统] 用户本轮新上传了一张图片"
                "（这就是「这张图」，改图只能用这一张）：\n" + note +
                f"- 本地路径：{image.get('local_path', '')}\n"
                f"- ComfyUI /input 文件名：{image.get('server_name', '')}\n"
                "规则：\n"
                "1) analyze_image 不传 path 就是分析这张（不要自己编路径）；\n"
                "2) 改图/参考图任务只能用这张，**不得**用「最近产物」里的"
                "历史图片代替，也不要用历史对话里的其它图片；\n"
                f"3) 模板的 image 参数填 server_name（{image.get('server_name', '')}），"
                "不要重新上传。\n"
                "先 analyze_image 看懂内容/风格/构图，再选 i2i（保持构图改内容/风格）"
                "或 style_transfer（锁姿势线条换画风）。\n"
                "若 analyze_image 失败（HTTP 错误/未配置视觉/被拦截）："
                "如实告诉用户你看不到这张图并给出原因，请其用文字描述画面或"
                "到 ⚙ 配置视觉模型；**禁止编造图片内容，禁止改用别的图片**；"
                "改图类请求到此停下，只有纯文字描述生图（t2i）才可继续。"})
        if memory:
            if self.verbose:
                self._log(f"[记忆] 召回 {memory.count('任务「')} 条相似任务经验")
            ev("memory_recalled", {"text": memory[:400]})
            self.history.append({"role": "user", "content": memory})

        final_reply = ""
        run_count = 0        # 确定性护栏：执行类工具调用次数
        # 任务契约 + 尝试台账：把"用户要什么 / 已经试过什么"变成机器可检查的状态
        from .task import TaskContract, TaskState
        contract = TaskContract.parse(user_message, uses_upload=bool(image))
        self.ctx.task = TaskState(contract)
        if contract.constraints:
            ev("stage", {"stage": "thinking", "detail": {
                "contract": contract.constraints}})
        interrupted = ""
        # 代码新鲜度：启动时（Brain 构造）取指纹，每回合开始比对。
        # 改完代码没重启服务时，旧逻辑会在用户毫无察觉的情况下继续跑
        # （项目 6 事故根因：新增的输入文件声明没生效 → 图片没上传 → 连败两次）
        from comfy_agent.freshness import changed_since, stale_message
        changed = changed_since(self._code_fp)
        self.ctx.stale_code = bool(changed)
        if changed:
            msg = stale_message(changed)
            self._log(f"[陈旧代码] {'、'.join(changed)}")
            ev("stage", {"stage": "warning", "detail": {"warning": msg}})
            self.history.append({"role": "user", "content":
                f"[system] {msg}\n"
                "你可以照常与用户对话、解释结果，但**不要调用任何生成类工具**"
                "（run_template/run_workflow/submit）；若用户要求出图，"
                "请先请其重启服务。"})
        try:
            final_reply = self._tool_loop(run_count)
        except Exception as e:
            # 回合不变量：任何异常都必须变成"给用户的一句解释 + 一条任务报告"。
            # 项目 5 里 provider SSL EOF 让 handle() 直接抛出，那一轮既没有交付
            # 也没有任何可复盘的痕迹——这是"某轮没有回复"的真正机制。
            import traceback
            from .policy import classify, describe
            kind = classify(str(e))
            interrupted = (f"⚠ 本轮没能完成：{describe(kind, str(e))}")
            self._log(f"[中断] {type(e).__name__}: {e}")
            ev("error", {"message": f"{type(e).__name__}: {str(e)[:300]}",
                         "kind": kind,
                         "traceback": traceback.format_exc()[-800:]})
            if kind == "transient":
                interrupted += "（服务商瞬时故障，直接回复「继续」即可重试）"
            elif kind == "config":
                interrupted += ("若刚换了服务商或 Key，多半是余额/配置问题；"
                                "到 ⚙ 里检查后再继续。")
        else:
            final_reply = self._tool_loop(run_count)

        # 任务闭环：本轮真的生成了产物才沉淀技能（失败经验也记录）
        self._auto_remember(user_message)
        text = final_reply or self._fallback_summary()
        if interrupted:
            text = (text.rstrip() + "\n\n" + interrupted).strip()
        task = self.ctx.task
        if task is not None and task.phase == "awaiting_user":
            # 有弹窗在等用户决定时也必须交付本轮回复（项目 5 里
            # "要修手"那一回合没有留下任何回复，且日志无法解释——
            # 这里把"必有回复"变成不变量，而不是靠运气）
            text += ("\n\n（我已在弹窗里请求下载缺失模型，等你在弹窗里决定；"
                     "确认后我会继续，拒绝就按缺模型的降级方案走。）")
        if task is not None:
            task.set_phase("delivered")
        out = {"text": text,
               "outputs": self.ctx.draft_meta.get("last_outputs", [])}
        if task is not None:
            out["task"] = {"renders": task.renders, "wasted": task.wasted,
                           "constraints": task.contract.constraints}
        ev("delivery", out)
        # 回合收尾留痕：任务报告 + turn_end（缺一环就能从日志直接看出来）
        try:
            report_path = self._write_task_report(task)
            ev("turn_end", {"phase": (task.phase if task else "n/a"),
                            "reply_len": len(text),
                            "renders": (task.renders if task else 0),
                            "wasted": (task.wasted if task else 0),
                            "report": report_path})
        except Exception:
            pass
        return text

    def _tool_loop(self, run_count: int) -> str:
        """工具循环：流式思考 → 解析调用 → 执行 → 观察，直到模型不再调工具。

        独立成方法是为了让 handle() 能用 try/except 包住它：任何异常都必须
        变成"给用户的一句解释 + 一条任务报告"（回合不变量）。
        """
        from .events import emit as ev
        final_reply = ""
        for turn in range(MAX_TURNS):
            reply = self._chat_streamed()
            self.history.append({"role": "assistant", "content": reply})

            # 提取全部工具调用（一条消息最多 3 个，按出现顺序执行——
            # 实测模型常把 analyze_image + run_template 写进同一条消息）
            calls = _parse_tool_calls(reply)
            if not calls:
                # 没有工具调用 = 大脑认为可以交付了
                final_reply = reply
                break
            for name, args in calls:
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
                        # 固定种子重试等于重跑同一张图：强制换随机种子，
                        # 否则"修改后重试"看不出差异（实测视频重试固定 12345）
                        prm = args.get("params")
                        if isinstance(prm, dict) and prm.get("seed"):
                            prm["seed"] = 0
                        self.history.append({"role": "user", "content":
                            "[system] 该 run_template 参数组合已执行过且未能解决问题。"
                            "禁止原样重试：必须改变策略（换节点/换参数/换工具/"
                            "search_nodes 找新方案）或 ask_user 询问用户。"
                            "（固定种子已自动改为随机，便于比较差异）"})
                # 护栏（结构性）：同模板 + 同基底图的"重掷"预算。
                # 只改 denoise/seed 即换签名，旧护栏拦不住；实测项目 5 因此
                # 连着重绘 3 次（6→4→6→6）纯烧 GPU。这里直接在引擎层拦住。
                if name == "run_template" and isinstance(args, dict) \
                        and self.ctx.task is not None:
                    from .task import strategy_signature
                    sig2 = strategy_signature(args.get("template_id"),
                                             args.get("params") or {})
                    verdict = self.ctx.task.ledger.check(sig2)
                    if not verdict["allow"]:
                        # 自动改走局部修复：局部问题只试过整图重绘时，别再重掷，
                        # 直接跑 local_repair（hand/face 自动生成遮罩）。
                        auto = self._auto_local_repair(reason=verdict["reason"])
                        if auto is not None:
                            result = auto
                            self.history.append({"role": "user", "content":
                                "[observation] 已自动改走局部修复："
                                + json.dumps(result, ensure_ascii=False,
                                             default=str)[:900]})
                            continue
                        self.ctx.task.wasted += 1
                        self.ctx.task.notes.append(verdict["reason"])
                        alts = "；".join(verdict.get("alternatives") or [])
                        self.history.append({"role": "user", "content":
                            f"[system] 已拦截一次无效重绘：{verdict['reason']}。"
                            f"可选做法：{alts}。"
                            + ("本机若缺检测器/遮罩来源，请如实告知用户并"
                               "ask_user 索取遮罩或改用整图之外的替代方案。"
                               if verdict["action"] == "blocked" else "")})
                        ev("tool_end", {"tool": name, "ok": False,
                                        "blocked": True,
                                        "summary": verdict["reason"][:200]})
                        continue
                    if verdict["action"] == "must_change":
                        self.history.append({"role": "user", "content":
                            f"[system] 提示：{verdict['reason']}"})
                # 护栏：超过 4 次执行（首次+3次修复）强制交付，避免无限烧GPU
                if run_count > 4:
                    self.history.append({"role": "user", "content":
                        "[system] 已执行 %d 次生成（首次+3次修复），达到硬性上限。"
                        "不要再调用执行类工具，基于已有结果完成交付总结。" % run_count})
                    break
                if self.verbose:
                    self._log(f"[工具] {name} "
                              f"{json.dumps(args, ensure_ascii=False)[:120]}")
                # 陈旧代码下不跑生成类工具：用旧逻辑出的图既不可信也白烧 GPU，
                # 明确报错让用户重启（项目 6 事故的直接教训）
                if getattr(self.ctx, "stale_code", False) and name in (
                        "run_template", "run_workflow", "submit"):
                    self.history.append({"role": "user", "content":
                        "[system] 引擎代码已更新但服务未重启，已拒绝执行 "
                        f"{name}。请告知用户重启 Web UI 后再试。"})
                    ev("tool_end", {"tool": name, "ok": False,
                                    "blocked": True,
                                    "summary": "引擎代码已更新，请重启服务"})
                    continue
                ev("tool_start", {"tool": name, "args": args})
                draft_before = json.dumps(self.ctx.draft, default=str) \
                    if self.ctx.draft else None
                result = execute_tool(self.ctx, name, args)
                # 台账：记录这次尝试（分数取自引擎评估，供"不提升就停"）
                if isinstance(result, dict) and self.ctx.task is not None:
                    if name in ("run_template", "run_workflow", "submit"):
                        ev_res = result.get("evaluation") or {}
                        score = (ev_res.get("vlm") or [{}])[0].get("score") \
                            if ev_res.get("vlm") else None
                        from .task import strategy_signature
                        from .task import AttemptLedger as _AL
                        # 遮罩为空 = 没定位到目标：这次"局部修复"其实什么都没改，
                        # 不算"修过但没修好"，也就不占手法额度（按技术失败记）
                        mask_bad = bool(result.get("mask_invalid"))
                        self.ctx.task.ledger.record(
                            strategy_signature(args.get("template_id", name),
                                               args.get("params") or {}),
                            args.get("params") or {}, result, score=score,
                            reason=str(result.get("error") or "")[:120],
                            technical=_AL.is_technical(result) or mask_bad)
                        self.ctx.task.renders += 1
                        if mask_bad:
                            note = (result.get("mask_check") or {}).get("reason")
                            self.ctx.task.notes.append(f"遮罩无效：{note}")
                            self.history.append({"role": "user", "content":
                                "[system] 本次局部修复的遮罩是空的（没定位到要修的"
                                "区域），遮罩区实际**没有被重绘**——不要把这次当成"
                                "修复成功，也不要拿它去交付。请如实告诉用户"
                                "「没定位到目标」，并请其上传黑白遮罩（白色=要重绘"
                                "区域）或给出比例坐标；换其它 target 也一样，"
                                "本机对这类内容没有可靠的自动检测器。"})
                # 待用户决策（如下载确认）：记状态，且本轮必须给出回复
                if isinstance(result, dict) and result.get("awaiting_confirm"):
                    self.ctx.task and self.ctx.task.set_phase("awaiting_user")
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
                    self._log(f"  -> {obs[:160]}")
                # 草稿变更 → 推送工作流图
                if self.ctx.draft is not None:
                    draft_now = json.dumps(self.ctx.draft, default=str)
                    if draft_now != draft_before:
                        ev("workflow_update", {
                            "graph": self.ctx.draft,
                            "meta": self.ctx.draft_meta})
            self._compact_history()
        return ""

    def _fallback_summary(self) -> str:
        """轮数用尽时的兜底汇报：说清产物与评估结论，而不是"请继续提要求"。

        实测这样一轮里其实已经产出了合格图（评估 8/10），却只回了一句
        "（达到最大轮数，请继续提出要求）"——用户拿不到任何有用信息。
        """
        parts = ["本轮任务已完成，先给你看当前结果："]
        outs = [str(p) for p in (self.ctx.draft_meta.get("last_outputs") or [])]
        if outs:
            names = [o.replace("\\", "/").rsplit("/", 1)[-1] for o in outs[-3:]]
            parts.append("产物：" + "、".join(names))
            parts.append("位置：" + outs[-1])
        res = self.ctx.draft_meta.get("last_eval") or {}
        if res:
            verdict = res.get("verdict")
            score = res.get("score")
            state = ("通过" if verdict is True
                     else "未通过" if verdict is False else "未知")
            parts.append(f"评估：{state}" + (f"（{score}/10）" if score is not None
                                            else ""))
        task = self.ctx.task
        if task is not None and task.contract.constraints:
            parts.append("对照你的要求：" + "；".join(task.contract.constraints))
        if task is not None and task.wasted:
            parts.append(f"（本轮拦截了 {task.wasted} 次无效重绘）")
        if not outs:
            parts.append("本轮没有产出可用图片，请补充要求或让我换个做法")
        return "\n".join(parts)

    def _auto_local_repair(self, reason: str = "") -> dict | None:
        """自动改走局部修复（局部问题不再整图重掷）。

        五个条件**全满足**才动手（避免误触发）：
          ① 契约判定这是局部修复（"修手/修脸/修局部/去掉…"）
          ② 台账显示只试过整图（unrepaired_local_fix）
          ③ 最近一次评估不达标或分数偏低
          ④ 有可用的基底图（上一版产物或本轮上传）
          ⑤ 世界模型确认目标路线的节点在本机存在
        任何一条不满足 → 返回 None（交回原逻辑：提示大脑换手法或问用户）。

        目标推断：手/指→hand，脸/面/五官→face，明确给了比例框→box，
        都不匹配 → 需要用户提供遮罩（policy.USER_INPUT 口径）。
        """
        task = self.ctx.task
        if task is None:
            return None
        contract = task.contract
        if not contract.is_local_fix:
            return None
        if not task.ledger.unrepaired_local_fix():
            return None
        last_eval = self.ctx.draft_meta.get("last_eval") or {}
        verdict = last_eval.get("verdict")
        score = last_eval.get("score")
        if verdict is not False and not (isinstance(score, (int, float))
                                         and score < 7):
            return None
        base = self._local_repair_base_image()
        if not base:
            return None
        target = self._infer_repair_target(contract.source_text)
        if target is None:
            self.history.append({"role": "user", "content":
                "[system] 这是局部修复，但既检测不到可自动定位的目标"
                "（手/脸），也没有坐标或自备遮罩 → 请 ask_user 让用户上传"
                "黑白遮罩（白色=要重绘区域），不要整图重绘、也不要假装修好。"})
            return None
        from .tools import _world_route_ok
        ok, why = _world_route_ok(self.ctx, "local_repair", target)
        if not ok:
            self.history.append({"role": "user", "content":
                f"[system] 局部修复路线（{target}）在本机不可用：{why}。"
                "请如实告知用户并请其提供黑白遮罩，不要整图重绘。"})
            return None
        # denoise 0.85：普通 SDXL 不是 inpaint 模型，VAEEncodeForInpaint 会用灰填充
        # 遮罩区；实测 denoise 0.65 会留下灰块、0.85 正常（灰块把结果毁成 3/10）
        params = {"image": base, "target": target, "denoise": 0.85,
                  "prompt": self._repair_prompt(target, contract.source_text)}
        if target == "box":
            params["box"] = self._infer_repair_box(contract.source_text) or ""
        from .tools import execute_tool
        from .task import strategy_signature
        sig = strategy_signature("local_repair", params)
        v = task.ledger.check(sig)
        if not v["allow"] and v["action"] == "blocked":
            return None
        self._log(f"[自动局部修复] target={target} base={base}（原因：{reason}）")
        self.ctx.draft_meta.pop("last_eval", None)
        result = execute_tool(self.ctx, "run_template",
                             {"template_id": "local_repair", "params": params})
        self.ctx.task.renders += 1
        ev_res = (result or {}).get("evaluation") or {}
        sc = (ev_res.get("vlm") or [{}])[0].get("score") if ev_res.get("vlm") else None
        from .task import AttemptLedger
        task.ledger.record(sig, params, result or {}, score=sc,
                           reason="自动局部修复",
                           technical=AttemptLedger.is_technical(result or {}))
        mc = (result or {}).get("mask_check") or {}
        if (result or {}).get("mask_invalid"):
            task.notes.append(f"遮罩无效：{mc.get('reason')}")
        # 路线在运行时不可用（如节点缺 python 依赖）：如实说明，别装作修了
        exec_err = str((result or {}).get("exec_error") or "")
        stage = str((result or {}).get("stage") or "")
        if stage in ("execution_failed", "repair_failed", "render_failed") \
                and exec_err:
            from .policy import classify, describe
            kind = classify(exec_err)
            task.notes.append(f"局部修复路线执行失败：{exec_err[:120]}")
            task.wasted += 1
            self.history.append({"role": "user", "content":
                f"[system] {target} 路线本机跑不起来：{describe(kind, exec_err)}。"
                + ("这属于依赖/节点问题（不是模型缺失）——不要让用户去下模型；"
                   "请如实告知需要补什么，并请其上传黑白遮罩（白色=重绘区域）"
                   "或改用不需要遮罩的做法。"
                   if kind == "missing_node" else
                   "请如实告知失败原因，并请用户决定是否上传遮罩重试。")})
        return result

    def _local_repair_base_image(self) -> str:
        """要修的基底图：优先上一版产物，其次本轮上传的原图。"""
        outs = [str(p) for p in (self.ctx.draft_meta.get("last_outputs") or [])
                if p]
        if outs:
            return outs[-1]
        up = (self.ctx.current_upload or {}).get("local_path") or ""
        return str(up)

    @staticmethod
    def _infer_repair_target(text: str) -> str | None:
        """从需求文字推断局部修复目标；推不出来返回 None（让用户给遮罩）。

        注意：只有**真的给了比例框**才算 box —— 光说「那块/局部」而没有坐标，
        模板只能猜一个居中框，那不是修复而是乱改，必须转 ask_user。
        """
        t = str(text or "").lower()
        if any(k in t for k in ("手", "指", "hand", "finger")):
            return "hand"
        if any(k in t for k in ("脸", "面", "五官", "眼", "嘴", "face", "eye")):
            return "face"
        if Brain._infer_repair_box(text):
            return "box"
        return None

    @staticmethod
    def _infer_repair_box(text: str) -> str | None:
        """从文字里找 'x,y,w,h' 比例框（0-1）；找不到返回 None。"""
        import re
        m = re.search(r"(\d*\.?\d+)\s*[,，]\s*(\d*\.?\d+)\s*[,，]\s*"
                      r"(\d*\.?\d+)\s*[,，]\s*(\d*\.?\d+)", str(text or ""))
        if not m:
            return None
        vals = [float(x) for x in m.groups()]
        if any(v > 1.0 for v in vals):      # 像是像素值 → 不猜比例
            return None
        return ",".join(str(v) for v in vals)

    @staticmethod
    def _repair_prompt(target: str, text: str) -> str:
        """局部修复的提示词：只描述要重绘的区域该长什么样。"""
        base = {
            "hand": "perfect hands, five fingers, natural anatomy, "
                    "clean lineart, same art style",
            "face": "clean face, natural eyes, symmetric features, "
                    "same art style",
            "box": "clean detail, consistent with surroundings, "
                   "same art style",
            "provided": "clean detail, consistent with surroundings, "
                        "same art style",
        }.get(target, "clean detail, same art style")
        return base

    def _write_task_report(self, task) -> str:
        """把本轮任务状态落盘（可观测性：以后不必人工翻 9000 行日志）。"""
        if task is None:
            return ""
        proj = getattr(self.ctx, "project", None)
        if proj is None:
            return ""
        try:
            import time as _t
            d = proj.dir / "task_reports"
            d.mkdir(parents=True, exist_ok=True)
            p = d / f"task_{_t.strftime('%Y%m%d_%H%M%S')}.json"
            p.write_text(json.dumps(task.to_dict(), ensure_ascii=False,
                                    indent=1, default=str), encoding="utf-8")
            return str(p)
        except Exception:
            return ""

    # ---------- 内部 ----------
    def _chat_streamed(self) -> str:
        """流式对话：逐块 emit think_delta，返回完整文本。

        主循环关闭思考模式（thinking=False）：glm 思考模型的工具调用意图
        会随机分流到 reasoning/content 两通道，而本循环只解析 content——
        开启思考会导致调用丢失与预算被 reasoning 挤占（实测两种故障模式）。
        行动决策不需要推理链，结构化任务关思考已验证质量良好。

        瞬时报错自动退避重试一次：provider 常态抽风（实测 502/503/504/SSL EOF），
        一次抖动就中断整轮会让用户收不到任何回复（项目 5 实证）。
        """
        from .events import emit as ev
        from .policy import TRANSIENT, classify
        last = None
        for attempt in (1, 2):
            parts = []
            reasoning = []
            try:
                for channel, delta in self.llm.chat_stream(
                        self.history, temperature=0.4, thinking=False):
                    if channel == "content":
                        parts.append(delta)
                    else:
                        reasoning.append(delta)
                    ev("think_delta", {"channel": channel, "delta": delta})
                text = _strip_think_blocks("".join(parts))
                if text.strip():
                    return text
                # content 为空但 reasoning 有内容：有的推理模型把答案全放进
                # reasoning 通道（实测火山 glm-5-3-flash）。把 reasoning 当正文，
                # 否则大脑"言之无物"，工具解析也会丢失。
                rt = _strip_think_blocks("".join(reasoning)).strip()
                if rt:
                    return rt
                last = LLMError("模型返回为空")
                if attempt == 1 and classify(str(last)) == TRANSIENT:
                    time.sleep(2.0)
                    continue
                return ""
            except LLMError as e:
                last = e
                if attempt == 1 and classify(str(e)) == TRANSIENT:
                    if self.verbose:
                        self._log(f"  LLM 瞬时故障，退避重试一次：{str(e)[:80]}")
                    time.sleep(2.0)
                    continue
                raise
        raise last

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
        always_inject = ("prompts.md", "t2i.md", "video.md", "repair.md")
        skill_docs = []
        for f in sorted(skills_dir.glob("*.md")):
            if f.name in always_inject:
                skill_docs.append(f.read_text(encoding="utf-8").strip())
        skills_blob = "\n\n---\n\n".join(skill_docs)
        # 能力→节点偏好（运行时按本机节点+模型双重核验，随机器变化）
        try:
            from comfy_agent.nodes_prefs import capability_summary
            capability_blob = capability_summary(self.ctx.knowledge)
        except Exception:
            capability_blob = ""
        # 本项目历史产物：让大脑知道"上一轮生成过什么"（链式任务用）。
        # 措辞刻意压低优先级：曾因这份列表里全是现成路径，大脑在看图失败后
        # 拿历史产物顶替用户刚上传的图（"根据历史记录这张图与之前相同"）。
        recent_blob = ""
        try:
            proj = getattr(self.ctx, "project", None)
            root = proj.outputs_dir() if proj else None
            if root and root.exists():
                items = [f for f in root.rglob("*")
                         if f.is_file() and f.suffix.lower() in
                         (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm",
                          ".mkv", ".mov")]
                items.sort(key=lambda f: f.stat().st_mtime, reverse=True)
                if items:
                    names = [f.name for f in items[:6]]
                    recent_blob = ("## 本项目历史产物（**仅**在用户明确要求"
                                   "「基于上次那张图/上一版继续」时才用；"
                                   "不要用它代替用户本轮上传的图）\n- "
                                   + "\n- ".join(names))
        except Exception:
            recent_blob = ""
        tool_call_example = '{"tool": "工具名", "args": {}}'
        self.history = [{"role": "system", "content": f"""你是 ComfyUI 生成任务的大脑，帮用户完成图像/视频生成。目标：把用户的自然语言变成高质量的生成结果。

## 可用模板
{tpl_lines}

{capability_blob}

{recent_blob}

## 任务→工具映射（严格遵守，先匹配再动手；绝大多数请求是单步生成！）
- **单步生成（"画X"、"生成X"、"生成一张XX风格的图"）→ 直接 run_template(t2i/i2i/style_transfer...)。这是最高频路径，不要为了简单任务去 scaffold/synthesize**
- **多步管线（"先生成再放大"、"画X然后放大"、"生成后加XX步骤"）→ 必须用 compose**：
  steps=[{{"template_id":"t2i","params":{{...}}}}, {{"template_id":"upscale_pass","params":{{"prompt":"画面内容简述"}}}}]
  禁止顺序调用两次 run_template 代替管线
- 模板上加节点（"加LoRA"、"改结构"）→ synthesize + propose_edit
- **锁构图/锁姿势的转绘（"用XX图做ControlNet引导"、"保持构图换内容"）→ style_transfer 模板，control_type=canny（离线可用）；只有模板做不到的结构变化才用 synthesize**
- 传图修改（"把这张图改成X风格"）→ analyze_image 先看图（本轮有上传就不传 path）→ i2i/style_transfer，image 参数填本轮上传的 server_name
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

## 工具（用 ```json 代码块调用，格式 {tool_call_example}；一条消息最多给 3 个工具调用，按出现顺序执行）
{tools_schema_for_llm()}

## 工作规则
1. **先理解再动手**：需求含糊（如"画张图"没说画什么）时用 ask_user 澄清；信息足够就直接执行。
2. **先看再干**：任务涉及图片（改图/风格转换/参考某张图）时，必须先 analyze_image 看懂图片（内容/风格/构图），再选模板写提示词——不要盲猜图片内容。**用户本轮上传了图时，analyze_image 不要传 path**（系统默认分析那张）；只看一次，别对同一张图反复分析。
3. **提示词由你写，且必须具体**：严格按 prompts.md 的分段结构写（图像八段式/视频四段式）；每项写"画面里能看到什么"，禁止 beautiful/nice/detailed 这类空词；重要元素用权重语法 (词:1.2)；用户没提负面词就保留家族默认负面（引擎会自动补默认项，别整段重写）。
10. **言外之意**：读懂用户没说出口的需求并补全：头像→1:1/3:4 特写+修脸步骤；海报/封面→竖版+构图留白+negative 防文字水印；壁纸→16:9+主体偏侧留空；证件照→纯色背景+正面+均匀光；商品图→纯色棚拍背景；"同角色多张"→固定 seed 与角色描述块复用；"改季节/时间"→i2i+光影词；"N秒视频"→按 video.md 分段。做完主线后主动检查这些隐含项是否已满足。
11. **能力优先**：需要某功能（修脸/锁姿势/放大/抠图等）先看上方的"能力→节点偏好"表，用表中本机可用的链路；表里没有或不可用再 search_nodes 探索，探索前先 read_skill(families/<对应家族>)。
12. **失败处理的顺序（硬性）**：①先**修参数**（同名模板重试，但必须改动具体参数并说明改了什么）→ ②再**换等价工具/模板** → ③都不行才**降级需求**，且必须明确告知用户"原请求未满足、降级成了什么"。**严禁把局部操作改成整体重新生成**（实测："把这张图放大两倍"变成重新画一张、"取最后一帧"变成重生成视频）——这类改法能得到产物，但不是用户要的东西，属于失败。
8. 模型/节点不确定时用 list_models / search_nodes / learn_node 查询，不要猜。
4. **成本意识（GPU 时间是真金白银）**：提交前必须经过本地校验；视频任务先告知预计耗时再执行；批量>4张先告知。
5. **评估闭环**：图像任务完成后用 view_image 评估（criteria 写用户的核心要求；批量>3张时传 sample=3 抽检）。注意 view_image 的 ok 只表示评估动作成功，**评估结论看 pass_overall**（false=不达标）。不达标时按 issues[].fix_hint 修改参数重试（最多一次，不要无限循环）。
6. **增量迭代**：用户反馈"改XX"时用 edit_workflow（class_type 定位节点，如提示词节点是 CLIPTextEncode）修改上一版，不要从头重建。
7. **交付格式**：完成时用中文总结：做了什么（模板/关键参数）→ 产物在哪（本地路径）→ 评估结论。不再调用工具时输出纯文本即结束。
8. 模型/节点不确定时用 list_models / search_nodes / learn_node 查询，不要猜。
9. **模型自动适配**：图像模板（t2i/i2i/style_transfer/upscale_pass）的 checkpoint 会自动绑定本机模型——除非用户点名用某模型，否则不要传 ckpt 参数；技能文档里出现具体模型名只是开发机示例，本机缺失时引擎会自动换成同家族模型（settings.json 的 model_prefs 可指定偏好）。
13. **缺模型先找来源再问用户**：执行结果报 `missing_models` 时，先 `search_models(filename=<缺的文件名>, folder=<models 子目录>)` 查可下载来源（本机 Manager 目录 + HF/hf-mirror/Civitai/ModelScope），再用 `download_model` 请求下载——**url/filename/folder/size 必须原样照抄候选字段**：不要自己拼 HuggingFace 地址（实测拼出来的都是 404），也不要自己估算大小（实测把 4.71MB 写成 1.2GB）。系统会弹出确认弹窗（显示名称/大小/来源/是否适配/目标目录），**由用户决定下载与否**；工具会立刻返回，此时不要重复调用、不要自己假设用户同意。用户同意→系统自动下载并重跑刚才失败的任务；拒绝或下载失败→按规则 12 走缺模型降级，并明确告诉用户缺哪个文件、应该放到本机哪个目录。
14. **看不到就必须说看不到（硬性）**：`analyze_image` 返回 `ok: false`（HTTP 错误 / 未配置视觉 / 图片不存在 / 被内容安全拦截）时，你**没有看过这张图**。此时：① 如实告诉用户看图失败并附上工具给的原因；② **禁止编造图片内容**（不许写"根据历史记录这张图应该是…"）；③ **禁止用本项目历史产物或历史对话里的其它图片顶替**本轮上传的图；④ 改图/参考图/风格转换类请求**到此停下**，请用户用文字描述画面、或到 ⚙ 里配置视觉模型；只有用户本来就要"纯文字描述生图"（t2i）时才可继续，并在交付时说明"没看到原图，是按文字描述生成的"。

## 领域技能库（节点速查 + 建图方法论，自由合成必读）
{skills_blob}"""}]

    def _log(self, msg: str):
        """安全日志：GBK 控制台遇到不可编码字符仅替换，绝不抛异常。

        曾因 '↳' 这类装饰字符在 cp936 控制台抛 UnicodeEncodeError，
        异常穿出 handle() 打死会话线程，项目永久卡在 thinking。"""
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        try:
            print(str(msg).encode(enc, "replace").decode(enc, "replace"),
                  flush=True)
        except Exception:
            pass

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


MAX_CALLS_PER_MESSAGE = 3


def _parse_tool_calls(reply: str, limit: int = MAX_CALLS_PER_MESSAGE):
    """提取一条消息里的全部工具调用（按出现顺序，上限 limit）。

    兼容三种形式：```json 围栏（可多个）、裸 JSON 行、工具名文本兜底。
    实测模型常在一条消息里写 2 个调用（analyze_image + run_template），
    旧版只执行第一个，第二个被丢进正文——这里全部提取、按序执行。"""
    calls = []
    # 1) 围栏形式（全部匹配）
    for m in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", reply, re.S):
        try:
            data = json.loads(m.group(1))
            name = _tool_name(data)
            if name:
                calls.append((name, data.get("args", {})))
        except json.JSONDecodeError:
            pass
        if len(calls) >= limit:
            return calls
    # 2) 无围栏：逐行尝试解析含 tool/task 键的 JSON
    # （先剔除已消费的围栏块，避免同一调用被围栏+裸行解析两次）
    bare_text = re.sub(r"```(?:json)?\s*\{.*?\}\s*```", " ",
                       reply, flags=re.S)
    for line in bare_text.splitlines():
        line = line.strip()
        if not line.startswith("{") or '"tool"' not in line and '"task"' not in line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = _tool_name(data)
        if name:
            calls.append((name, data.get("args", {})))
        if len(calls) >= limit:
            return calls
    if calls:
        return calls
    # 3) 文本兜底：tool_name 且无参数的工具
    _init_tool_names()
    for tname in TOOL_NAME_INDEX:
        if re.search(rf"\b{tname}\b", reply):
            return [(tname, {})]
    return []


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
