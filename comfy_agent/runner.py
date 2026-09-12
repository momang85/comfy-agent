# -*- coding: utf-8 -*-
"""通用执行引擎：任何 API 格式工作流走同一条五段管线。

run_workflow(workflow_api) —— 唯一执行入口（模板渲染/图合成/文件转换通用）：
  stage1 本地校验 ──失败──→ {stage: validation_failed, issues}
  stage2 确定性自动修复(≤3轮) ──修复不完──→ {stage: repair_failed, repairs, unfixable}
  stage3 提交（服务器校验失败→参数级修复→重试≤3）
  stage4 执行（OOM/形状→建议+一次自动重试）──仍失败──→ {stage: execution_failed, ...}
  stage5 下载产物 → {stage: completed, outputs}

失败阶段的结果结构化回流大脑，由大脑做语义级修复后重跑。
"""
from __future__ import annotations

import datetime
from pathlib import Path

from . import config
from .client import Client, ComfyUIError
from .knowledge import Knowledge
from .model_adapt import adapt_ckpt, checkpoint_exists
from .repair import (auto_repair, parse_server_validation_error,
                     parse_execution_error, suggest_for_execution_error,
                     friendly_error_zh)
from .templates import get_template
from .templates.base import _folders_for
from .validate import validate_workflow, ValidationIssue

# 确定性自动修复的最大轮数
MAX_DETERMINISTIC_ROUNDS = 3


def _emit_stage(stage: str, detail: dict = None):
    """懒导入事件总线（避免 brain↔comfy_agent 循环依赖）。"""
    try:
        from brain.events import emit
        emit("stage", {"stage": stage, "detail": detail or {}})
    except Exception:
        pass


def run_workflow(workflow_api: dict, *, source: str = "workflow",
                 client: Client = None, knowledge: Knowledge = None,
                 wait: bool = True, timeout: float = 1800.0,
                 output_root=None, on_event=None) -> dict:
    """通用执行管线。返回统一 WorkflowResult：
    {stage, ok, source, prompt_id, repairs, validation_issues,
     server_errors, exec_error, suggestion, outputs, output_dir}"""
    client = client or Client()
    knowledge = knowledge or Knowledge.build()
    # 可选自愈：ComfyUI 不可达且 AUTO_START_COMFY=1 时尽力拉起（默认关闭）
    alive = getattr(client, "is_alive", None)
    if callable(alive):
        try:
            if not alive():
                from .guard import try_start_comfy
                if try_start_comfy():
                    _emit_stage("warning", {
                        "warning": "ComfyUI 不在运行，已按 AUTO_START_COMFY=1 自动拉起"})
        except Exception:
            pass
    wf = {k: dict(v) for k, v in workflow_api.items()}
    result = {"ok": False, "source": source, "stage": "validate",
              "repairs": [], "outputs": []}

    # ---- stage1+2: 本地校验 → 确定性自动修复（≤3轮） ----
    # 校验出问题先交给确定性修复（它能修 VAE缺失/枚举/范围/文件名等）；
    # 修复不掉才带完整诊断回流大脑做语义级修复。
    issues = validate_workflow(wf, knowledge)
    blockers = [i.to_dict() for i in issues
                if not (i.input_name and i.input_name.lower() == "image"
                        and i.kind in ("bad_enum", "missing_file"))]
    repair_log = []
    if blockers:
        for round_no in range(MAX_DETERMINISTIC_ROUNDS):
            wf, report = auto_repair(wf, knowledge)
            repair_log.extend(report.fixes)
            if report.ok:
                break
        else:
            _emit_stage("validation_failed", {
                "issues": blockers,
                "nodes": [str(i.get("node")) for i in blockers
                          if i.get("node")]})
            result.update({"stage": "validation_failed",
                           "repairs": repair_log,
                           "validation_issues": blockers,
                           "unfixable": report.unfixable,
                           "hint": "本地校验失败且确定性修复无法解决，"
                                   "请按 unfixable 做语义级修复（edit_workflow/"
                                   "propose_edit/learn_node）后重跑"})
            return result
        result["repairs"] = repair_log

    # ---- stage3: 提交（服务器校验失败→参数级修复→重试≤3） ----
    prompt_id = None
    server_errors = []
    for attempt in range(MAX_DETERMINISTIC_ROUNDS):
        try:
            r = client.prompt(wf)
            if r.get("node_errors"):
                errs = parse_server_validation_error(r)
                server_errors.extend(errs)
                fixed = _apply_server_fixes(wf, errs, knowledge)
                if not fixed:
                    _emit_stage("repair_failed",
                                {"detail": {"server": bool(server_errors)}})
                    result.update({"stage": "repair_failed",
                                   "repairs": repair_log,
                                   "server_errors": server_errors,
                                   "hint": "服务器校验失败且无法参数级修复"})
                    return result
                continue
            prompt_id = r["prompt_id"]
            break
        except ComfyUIError as e:
            errs = parse_server_validation_error(e.payload or {})
            server_errors.extend(errs)
            fixed = _apply_server_fixes(wf, errs, knowledge)
            if not fixed:
                result.update({"stage": "repair_failed",
                               "repairs": repair_log,
                               "server_errors": server_errors,
                               "hint": str(e)[:300]})
                return result
    if prompt_id is None:
        _emit_stage("repair_failed")
        result.update({"stage": "repair_failed", "repairs": repair_log,
                       "server_errors": server_errors})
        return result

    result.update({"stage": "running", "ok": True, "prompt_id": prompt_id})
    _emit_stage("running", {"prompt_id": prompt_id, "source": source})
    if not wait:
        return result
    # 从这里往下的任何失败都必须把 ok 改回 False：stage3 已把 ok 置 True，
    # 只改 stage 会让调用方（大脑/前端/CLI）把失败当成功（实测踩过）

    # ---- stage4: 执行（OOM/形状→一次自动重试） ----
    entry = client.wait_for_result(prompt_id, timeout=timeout)
    exec_err = parse_execution_error(entry)
    if exec_err:
        suggestion = suggest_for_execution_error(exec_err, wf)
        if suggestion and suggestion.get("applied"):
            if on_event:
                on_event(f"执行出错，自动降参重试: {suggestion}")
            r2 = client.prompt(wf)
            entry = client.wait_for_result(r2["prompt_id"], timeout=timeout)
            exec_err = parse_execution_error(entry)
            result["prompt_id"] = r2["prompt_id"]
            if exec_err:
                _emit_stage("execution_failed", {"exec_error": str(exec_err)[:200]})
                result.update({"stage": "execution_failed", "ok": False,
                               "exec_error": exec_err,
                               "suggestion": suggestion,
                               "hint": friendly_error_zh(exec_err)})
                return result
        else:
            result.update({"stage": "execution_failed", "ok": False,
                           "exec_error": exec_err,
                           "suggestion": suggestion,
                           "hint": friendly_error_zh(exec_err)})
            return result

    # ---- stage5: 下载 ----
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_source = "".join(c if c.isalnum() or c in "-_" else "_"
                          for c in str(source))[:40]
    out_dir = (Path(output_root) if output_root else config.RESULTS_DIR) \
        / f"{safe_source}_{ts}"
    outs = client.outputs_of(entry, save_dir=out_dir)
    _emit_stage("completed", {"outputs": len(outs)})
    result.update({"stage": "completed", "ok": True,
                   "outputs": outs, "output_dir": str(out_dir)})

    # ---- stage6: 产物强制评估（引擎层保底，不依赖大脑是否记得）----
    _force_video_evaluation(result, outs)
    _force_image_evaluation(result, outs)
    return result


_VIDEO_EXTS = (".mp4", ".webm", ".mkv", ".mov")
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")


def _force_image_evaluation(result: dict, outs: list) -> None:
    """图像产物自动评估（EVAL_POLICY=off 时跳过；批量抽样 2 张）。

    大脑本应自行调 view_image 评估，但轻量模型经常跳过（实测连续 3 个
    出图任务都没评估），前端"评估"卡片长期空白——这里做引擎级保底，
    与视频产物一致。"""
    if config.EVAL_POLICY == "off":
        return
    images = [o.get("local_path") for o in outs
              if o.get("local_path")
              and str(o["local_path"]).lower().endswith(_IMAGE_EXTS)]
    if not images:
        return
    try:
        from brain.eval import evaluate
        from brain.events import emit
        eval_result = evaluate(images, "画面清晰完整、主体明确、无肢体或结构畸形、"
                               "无文字水印", sample=2)
        d = eval_result.to_dict()
        verdict = d.pop("ok", None)
        d["verdict"] = verdict
        result["evaluation"] = d
        emit("evaluation", {
            "kind": "image_forced", "verdict": verdict,
            "prompt_id": result.get("prompt_id"),
            "score": (d.get("vlm") or [{}])[0].get("score")
            if d.get("vlm") else None,
            "issues": [
                {"location": str(i.get("image", "")).replace("\\", "/").rsplit("/", 1)[-1],
                 "description": "；".join(
                     x.get("description", "")
                     for x in (i.get("issues") or [])
                     if isinstance(x, dict))[:120]}
                for i in d.get("vlm", []) or []
                if i.get("pass") is False][:4],
            "files": images})
    except Exception as e:
        result["evaluation"] = {"error": str(e)[:200]}


def _force_video_evaluation(result: dict, outs: list) -> None:
    """视频产物自动抽帧评估（EVAL_POLICY=off 时跳过）。
    懒导入 brain.eval（避免循环依赖，与 _emit_stage 同模式）。"""
    if config.EVAL_POLICY == "off":
        return
    videos = [o.get("local_path") for o in outs
              if o.get("local_path")
              and str(o["local_path"]).lower().endswith(_VIDEO_EXTS)]
    if not videos:
        return
    try:
        from brain.eval import evaluate_video
        from brain.events import emit
        eval_result = evaluate_video(videos[0], "视频帧质量良好，主体清晰，"
                                     "动作连贯无明显畸形", frames=4)
        d = eval_result.to_dict()
        verdict = d.pop("ok", None)
        d["verdict"] = verdict
        result["evaluation"] = d
        emit("evaluation", {"kind": "video_forced", "verdict": verdict,
                            "prompt_id": result.get("prompt_id"),
                            "score": (d.get("vlm") or [{}])[0].get("score")
                            if d.get("vlm") else None,
                            "issues": [
                                {"location": f"第{i.get('frame_index', 0) + 1}帧",
                                 "description": "；".join(
                                     x.get("description", "")
                                     for x in (i.get("issues") or [])
                                     if isinstance(x, dict))[:120]}
                                for i in d.get("vlm", []) or []
                                if i.get("pass") is False][:4],
                            "files": videos})
    except Exception as e:
        result["evaluation"] = {"error": str(e)[:200]}


def run_template(template_id: str, params: dict, **kw) -> dict:
    """模板路径的薄封装：渲染 → run_workflow。"""
    tpl = get_template(template_id)
    if tpl is None:
        _emit_stage("validation_failed",
                    {"error": f"未知模板: {template_id}"})
        return {"ok": False, "stage": "render_failed",
                "error": f"未知模板: {template_id}"}
    # 参数幻觉警告：大脑发明的参数名（duration/num_frames 等）不再被
    # 静默忽略——执行照常，但结果中显式告知哪些参数未生效
    known = {prm.name for prm in tpl.params()}
    params = dict(params or {})
    knowledge = kw.get("knowledge") or Knowledge.build()
    # 顺序：别名/单位归一 → 模型适配 → 参数护栏 → 提示词体检
    params, unit_notes = tpl.normalize_params(params)
    unknown = [k for k in params if k not in known]
    # 跨设备模型适配：默认/显式 checkpoint 本机不存在时，自动绑定本机模型
    params, adapt_notes = adapt_ckpt(tpl, params, knowledge)
    params, guard_notes = _guard_params(tpl, params)
    params, prompt_notes = _apply_prompt_spec(tpl, params)
    # 模型名参数归一（除 ckpt 外，如 LTX 的 text_encoder=Gemma 分片）
    params, model_notes = _canonical_model_params(tpl, params, knowledge)
    # 输入文件由引擎代传到 ComfyUI /input（大脑常漏这一步，见 problems-detailed P0-4）
    params, upload_notes, upload_err = _ensure_inputs_uploaded(
        tpl, params, kw.get("client"), kw.get("output_root"))
    if upload_err:
        _emit_stage("validation_failed", {"error": upload_err})
        return {"ok": False, "stage": "render_failed", "error": upload_err}
    ckpt_default = next((p.default for p in tpl.params()
                         if p.name == "ckpt"), None)
    ckpt_ok = bool(params.get("ckpt")) and \
        checkpoint_exists(knowledge, params["ckpt"])
    # 已适配的 checkpoint 不再算缺失；其余模型（controlnet/vae/视频文件等）
    # 缺失仍按缺模型报错
    missing = [m for m in tpl.models_used
               if not knowledge.find_model(m, folders=_folders_for(m))
               and not (ckpt_ok and m == ckpt_default)]
    all_notes = list(unit_notes) + list(adapt_notes) + list(guard_notes) \
        + list(prompt_notes) + list(upload_notes) + list(model_notes)
    if missing:
        _emit_stage("validation_failed",
                    {"error": f"缺少模型: {missing}"})
        early = {"ok": False, "stage": "render_failed",
                 "error": f"模板 {template_id} 缺少模型: {missing}",
                 "missing_models": missing,
                 "hint": ("图像模板会自动适配本机任意 SDXL/SD1.5 checkpoint"
                          "（可用 settings.json 的 model_prefs 指定偏好）；"
                          "视频模板需安装对应模型文件，见 README 模型要求")}
        if all_notes:
            early["warnings"] = all_notes      # 参数收敛/提示词体检结果别丢
            for w in all_notes:
                _emit_stage("warning", {"warning": w})
        return early
    try:
        wf = tpl.render(params)
    except Exception as e:
        _emit_stage("validation_failed",
                    {"error": f"渲染失败: {e}"})
        failed = {"ok": False, "stage": "render_failed",
                  "error": f"渲染失败: {e}"}
        if all_notes:
            failed["warnings"] = all_notes
        return failed
    result = run_workflow(wf, source=f"template:{template_id}", **kw)
    warnings = list(all_notes)
    if unknown:
        warnings.append(f"模板 {template_id} 不识别参数 {unknown}（已忽略）。"
                        f"支持参数: {sorted(known)}")
    for w in warnings:
        # detail 只包一层：前端读的是 data.detail.warning
        _emit_stage("warning", {"warning": w})
    if warnings:
        result.setdefault("warnings", []).extend(warnings)
    return result


def _canonical_model_params(tpl, params: dict, knowledge) -> tuple[dict, list[str]]:
    """把模板参数里的模型文件名归一为本机清单形态（分隔符/子目录差异）。

    与 adapt_ckpt 同理：本地校验对分隔符不敏感，但服务器按精确串校验
    （如 text_encoder='gemma-3-.../model-00001-...' 在 Windows 清单里是反斜杠）。"""
    notes: list[str] = []
    suffixes = (".safetensors", ".ckpt", ".pt", ".gguf", ".pth")
    for prm in tpl.params():
        if prm.name == "ckpt":
            continue                    # adapt_ckpt 已处理 checkpoint
        if not str(prm.default or "").lower().endswith(suffixes):
            continue
        val = params.get(prm.name) or prm.default
        if not val:
            continue
        canon = knowledge.resolve_model_name(str(val))
        if canon and canon != val:
            params[prm.name] = canon
            notes.append(f"{prm.name} 已归一为本机清单形态：{val!r} → {canon!r}")
    return params, notes


def _ensure_inputs_uploaded(tpl, params: dict, client, output_root=None):
    """模板声明的输入文件自动上传到 ComfyUI /input。

    两种情况都覆盖：
    1. 值是本机存在的文件路径 → 直接上传
    2. 值是"裸文件名"（大脑预判的 server 名，实际还没上传）→ 在本项目
       产物目录里找同名文件补传（实测大脑会先写 `agent_t2i_xxx.png`
       再补 upload，第一次必然被服务器 value_not_in_list 拒绝）
    返回值：(params, notes, error)。error 非空时调用方直接报错返回。"""
    decls = getattr(tpl, "input_files", None) or []
    notes: list[str] = []
    if not decls:
        return params, notes, None
    for pname, _kind in decls:
        val = params.get(pname)
        if not isinstance(val, str) or not val.strip():
            continue
        p = Path(val)
        if not p.is_file() and output_root:
            # 裸文件名兜底：在本项目产物目录里按文件名找
            try:
                cand = next((f for f in Path(output_root).rglob(p.name)
                             if f.is_file()), None)
            except Exception:
                cand = None
            if cand:
                p = cand
        if not p.is_file():
            continue          # 既不是本地文件也找不到 → 交给后续校验报错
        try:
            cli = client or Client()
            server_name = (cli.upload_image(p) or {}).get("name", p.name)
        except Exception as e:      # noqa: BLE001 - 上传失败要明确报给大脑
            return params, notes, (f"{pname} 上传到 ComfyUI /input 失败：{e}。"
                                   "请确认 ComfyUI 正在运行")
        params[pname] = server_name
        notes.append(f"{pname} 已自动上传到 ComfyUI /input："
                     f"{p.name} → {server_name}")
    return params, notes, None


def _param_of(tpl, name: str):
    return next((p for p in tpl.params() if p.name == name), None)


def _guard_params(tpl, params: dict) -> tuple[dict, list[str]]:
    """高危参数护栏：显著偏离模板推荐值时自动收敛并告警。

    实测最贵的一类错误是量级错误（视频 turbo 模型 cfg 被写到 7.5，推荐 3.0，
    结果是过曝发糊），比"参数名写错"更隐蔽。"""
    notes = []
    cfg = _param_of(tpl, "cfg")
    val = params.get("cfg")
    if cfg is not None and isinstance(val, (int, float)) and cfg.recommended:
        rec = float(cfg.recommended)
        if val > rec * 1.5:
            notes.append(f"cfg={val} 远高于该模板推荐值 {rec}，已收敛为 "
                         f"{rec}（蒸馏/低步数模型高 CFG 会过曝发糊）")
            params["cfg"] = rec
    return params, notes


def _apply_prompt_spec(tpl, params: dict) -> tuple[dict, list[str]]:
    """提示词体检（族规范）+ 负面词保底合并 + 分辨率合理性提醒。"""
    from .promptspec import check_prompt, prepare
    if _param_of(tpl, "prompt") is None or \
            not str(params.get("prompt") or "").strip():
        return params, []
    family = (getattr(tpl, "family", "") or "").strip() \
        or config.family_of(str(params.get("ckpt") or ""))
    if _param_of(tpl, "negative") is None:
        chk = check_prompt(family, params["prompt"], "")   # 无负面参数：只体检正向
        params["prompt"] = chk["prompt"]
        return params, chk["warnings"]
    prompt, negative, warns = prepare(
        family, params["prompt"], str(params.get("negative") or ""))
    params["prompt"] = prompt
    params["negative"] = negative
    w, h = params.get("width"), params.get("height")
    if family in ("sdxl", "sd15") and isinstance(w, int) and isinstance(h, int) \
            and min(w, h) < 768:
        warns.append(f"分辨率 {w}x{h} 低于 SDXL/SD1.5 原生区间（建议 1024 簇 / "
                     "SD1.5 用 512 但 SDXL 在 512 会明显掉质量）")
    return params, warns


def upload_input_image(path: str | Path, client: Client = None) -> str:
    """上传用户图片到 /input，返回服务器端文件名（模板 image 参数直接可用）。"""
    client = client or Client()
    r = client.upload_image(path)
    return r.get("name", Path(path).name)


def _apply_server_fixes(wf: dict, errs: list[dict], knowledge) -> list:
    """服务器校验错误的参数级自动修复（确定性）。"""
    fixes = []
    for e in errs:
        node_id = str(e.get("node") or "")
        node = wf.get(node_id)
        if node is None:
            continue
        if "not in choices" in (e.get("message") or ""):
            inp = e.get("input")
            if inp:
                choices = knowledge.enum_choices(node["class_type"], inp)
                if choices:
                    old = node["inputs"].get(inp)
                    node["inputs"][inp] = choices[0]
                    fixes.append({"node": node_id, "input": inp,
                                  "was": old, "now": choices[0]})
    return fixes
