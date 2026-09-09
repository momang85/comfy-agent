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
from .repair import (auto_repair, parse_server_validation_error,
                     parse_execution_error, suggest_for_execution_error,
                     friendly_error_zh)
from .templates import get_template
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
                result.update({"stage": "execution_failed",
                               "exec_error": exec_err,
                               "suggestion": suggestion,
                               "hint": friendly_error_zh(exec_err)})
                return result
        else:
            result.update({"stage": "execution_failed",
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

    # ---- stage6: 视频产物强制评估（引擎层保底，不依赖大脑是否记得）----
    _force_video_evaluation(result, outs)
    return result


_VIDEO_EXTS = (".mp4", ".webm", ".mkv", ".mov")


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
                    {"detail": {"error": f"未知模板: {template_id}"}})
        return {"ok": False, "stage": "render_failed",
                "error": f"未知模板: {template_id}"}
    # 参数幻觉警告：大脑发明的参数名（duration/num_frames 等）不再被
    # 静默忽略——执行照常，但结果中显式告知哪些参数未生效
    known = {prm.name for prm in tpl.params()}
    unknown = [k for k in (params or {}) if k not in known]
    knowledge = kw.get("knowledge") or Knowledge.build()
    missing = tpl.missing_models(knowledge)
    if missing:
        _emit_stage("validation_failed",
                    {"detail": {"error": f"缺少模型: {missing}"}})
        return {"ok": False, "stage": "render_failed",
                "error": f"模板 {template_id} 缺少模型: {missing}",
                "missing_models": missing}
    try:
        wf = tpl.render(params)
    except Exception as e:
        _emit_stage("validation_failed",
                    {"detail": {"error": f"渲染失败: {e}"}})
        return {"ok": False, "stage": "render_failed",
                "error": f"渲染失败: {e}"}
    result = run_workflow(wf, source=f"template:{template_id}", **kw)
    if unknown:
        warn = (f"模板 {template_id} 不识别参数 {unknown}（已忽略）。"
                f"支持参数: {sorted(known)}")
        result.setdefault("warnings", []).append(warn)
        _emit_stage("warning", {"detail": {"warning": warn}})
    return result


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
