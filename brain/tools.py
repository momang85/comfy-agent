# -*- coding: utf-8 -*-
"""大脑工具集：LLM 主循环可调用的领域工具（文本 JSON 协议）。

工具列表（12个）：
  list_templates / render_workflow / list_models / upload_image /
  validate / submit / wait_result / fetch_outputs / view_image /
  inspect_node / edit_workflow / ask_user
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from comfy_agent import config
from comfy_agent.client import Client, ComfyUIError
from comfy_agent.knowledge import Knowledge
from comfy_agent.templates import get_template, catalog
from comfy_agent.validate import validate_workflow
from comfy_agent.repair import auto_repair
from comfy_agent.runner import run_workflow, run_template

from .eval import evaluate, evaluate_video


class ToolContext:
    """工具共享状态（一次会话一个实例）。"""

    def __init__(self, ask_user_fn: Callable[[str], str] | None = None,
                 project=None):
        self.client = Client()
        self.knowledge = Knowledge.build()
        self.ask_user_fn = ask_user_fn
        # 项目隔离：产物输出到项目目录
        self.project = project
        self.project_id = project.id if project else None
        # 当前工作草稿（render_workflow 产出 / edit_workflow 修改）
        self.draft: dict | None = None
        self.draft_meta: dict = {}
        # 最近一次提交
        self.prompt_id: str | None = None
        self.history_entry: dict | None = None
        # 本轮任务轨迹（失败→修复→成功，供技能沉淀）
        self.trajectory: list[dict] = []
        # 本轮用户新上传的图（Brain.handle 写入）：分析/执行默认用它，
        # 避免"大脑拿项目历史产物当刚上传的图"（见 docs 的 D 系列缺陷）
        self.current_upload: dict | None = None
        # 合成会话（图合成引擎）
        self.synth = None

    # ---- 会话持久化 ----
    def save_session(self, path: Path):
        data = {"draft": self.draft, "draft_meta": self.draft_meta,
                "prompt_id": self.prompt_id}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                        encoding="utf-8")

    def load_session(self, path: Path):
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            self.draft = data.get("draft")
            self.draft_meta = data.get("draft_meta", {})
            self.prompt_id = data.get("prompt_id")


# ---------------- 工具实现 ----------------

def tool_list_templates(ctx: ToolContext, args: dict) -> dict:
    """列出全部可用模板（含参数声明）。"""
    return {"ok": True, "templates": catalog()}


def tool_render_workflow(ctx: ToolContext, args: dict) -> dict:
    """渲染模板为工作流。args: {template_id, params:{...}}"""
    tid = args.get("template_id", "")
    tpl = get_template(tid)
    if tpl is None:
        return {"ok": False, "error": f"未知模板 {tid}"}
    params = args.get("params", {})
    try:
        wf = tpl.render(params)
    except Exception as e:
        return {"ok": False, "error": f"渲染失败: {e}"}
    ctx.draft = wf
    ctx.draft_meta = {"template_id": tid, "params": params,
                      "family": tpl.family}
    # 渲染后立即本地校验（不花 GPU 时间）
    issues = validate_workflow(wf, ctx.knowledge)
    blockers = [i.to_dict() for i in issues
                if not (i.input_name and i.input_name.lower() == "image")]
    return {"ok": True, "template": tid, "nodes": len(wf),
            "validation_issues": blockers,
            "note": "草稿已保存（ctx.draft）。有 blocker 时请先处理。"}


def tool_list_models(ctx: ToolContext, args: dict) -> dict:
    """列出本机模型。args: {folder?: "checkpoints"}"""
    folder = args.get("folder")
    return {"ok": True, "models": ctx.client.models(folder)}


def tool_upload_image(ctx: ToolContext, args: dict) -> dict:
    """上传用户图片到 /input。args: {path}"""
    p = Path(args.get("path", ""))
    if not p.exists():
        return {"ok": False, "error": f"文件不存在: {p}"}
    try:
        r = ctx.client.upload_image(p)
        return {"ok": True, "server_name": r.get("name"),
                "note": "把 server_name 填入模板的 image 参数"}
    except ComfyUIError as e:
        return {"ok": False, "error": str(e)[:300]}


def tool_validate(ctx: ToolContext, args: dict) -> dict:
    """本地校验当前草稿（不提交）。"""
    if not ctx.draft:
        return {"ok": False, "error": "没有工作流草稿（先 render_workflow）"}
    issues = validate_workflow(ctx.draft, ctx.knowledge)
    return {"ok": True, "issues": [i.to_dict() for i in issues]}


def tool_submit(ctx: ToolContext, args: dict) -> dict:
    """提交草稿到服务器（校验+自动修复+入队）。args: {wait?: bool}"""
    from .events import emit
    if not ctx.draft:
        return {"ok": False, "error": "没有工作流草稿"}
    wf, report = auto_repair(ctx.draft, ctx.knowledge)
    if not report.ok:
        emit("stage", {"stage": "validation_failed",
                       "detail": {"issues": report.unfixable}})
        return {"ok": False, "error": "存在无法自动修复的问题",
                "repair": report.to_dict()}
    ctx.draft = wf
    try:
        r = ctx.client.prompt(wf)
    except ComfyUIError as e:
        return {"ok": False, "error": str(e)[:400],
                "payload": e.payload}
    ctx.prompt_id = r["prompt_id"]
    emit("stage", {"stage": "running",
                   "detail": {"prompt_id": ctx.prompt_id}})
    return {"ok": True, "prompt_id": ctx.prompt_id,
            "repairs": report.to_dict(),
            "note": "已入队。用 wait_result 等待完成。"}


def tool_wait_result(ctx: ToolContext, args: dict) -> dict:
    """等待执行完成。args: {timeout_sec?: 1800}"""
    from .events import emit
    if not ctx.prompt_id:
        return {"ok": False, "error": "没有待等待的任务（先 submit）"}
    timeout = float(args.get("timeout_sec", 1800))
    try:
        entry = ctx.client.wait_for_result(ctx.prompt_id, timeout=timeout)
    except ComfyUIError as e:
        return {"ok": False, "error": str(e)[:300]}
    ctx.history_entry = entry
    status = entry.get("status", {})
    if status.get("status_str") == "error":
        from comfy_agent.repair import parse_execution_error
        err = parse_execution_error(entry)
        emit("stage", {"stage": "execution_failed",
                       "detail": {"error": str(err)[:200]}})
        return {"ok": False, "error": "执行失败", "detail": err}
    emit("stage", {"stage": "completed", "detail": {"done": True}})
    return {"ok": True, "completed": True}


def tool_fetch_outputs(ctx: ToolContext, args: dict) -> dict:
    """取回执行产物（下载到本地，并更新产物清单供交付/画廊使用）。"""
    from .events import emit
    if not ctx.history_entry:
        if not ctx.prompt_id:
            return {"ok": False, "error": "没有已完成的任务"}
        hist = ctx.client.history(ctx.prompt_id)
        entry = hist.get(ctx.prompt_id)
        if entry is None:
            return {"ok": False, "error": "任务尚未完成（先 wait_result）"}
        ctx.history_entry = entry
    import datetime
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = (ctx.project.outputs_dir() if ctx.project else
               config.RESULTS_DIR) / f"session_{ts}"
    outs = ctx.client.outputs_of(ctx.history_entry, save_dir=out_dir)
    local = [o.get("local_path") for o in outs if o.get("local_path")]
    ctx.draft_meta["last_outputs"] = local
    emit("stage", {"stage": "completed", "detail": {"outputs": len(outs)}})
    return {"ok": True, "outputs": outs, "output_dir": str(out_dir),
            "local_paths": local}


def _latest_project_outputs(ctx: ToolContext, exts) -> list:
    """项目内最近一次产物（按 mtime 降序，最多 4 个）。

    实测 use_last 只看"最近一次运行"，中间一次失败就把引用覆盖掉，
    导致"项目里明明有 3 个 mp4 却报没有视频"。"""
    try:
        root = ctx.project.outputs_dir() if ctx.project else None
    except Exception:
        root = None
    if not root or not root.exists():
        return []
    files = [f for f in root.rglob("*")
             if f.is_file() and f.suffix.lower() in exts]
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return [str(f) for f in files[:4]]


def _engine_eval_reuse(ctx: ToolContext, paths: list) -> dict | None:
    """若引擎已对同一批产物评估且通过，返回可复用的结论（否则 None）。

    避免同一产物被评估两次且结论冲突（引擎通用 criteria vs 大脑任务
    criteria），也省掉一次 VLM 调用。"""
    le = ctx.draft_meta.get("last_eval") or {}
    if not le or le.get("verdict") is not True:
        return None
    target = {str(p) for p in paths}
    known = {str(p) for p in (le.get("files") or [])}
    if not target or not (target & known):
        return None
    return {"ok": True, "pass_overall": True, "skipped": True,
            "note": ("引擎已自动评估该产物并通过"
                     f"（分数 {le.get('score')}），未重复调用 VLM。"
                     "如需任务专属判定请显式传入 paths 重新评估。"),
            "engine_eval": le}


def tool_list_outputs(ctx: ToolContext, args: dict) -> dict:
    """列出本项目最近的产物文件（重启/压缩上下文后不猜路径）。

    args: {}（无参数）。实测大脑在丢失产物引用时会传占位符
    （如 "您的视频文件路径"）——给一个按需查询的只读工具。"""
    try:
        root = ctx.project.outputs_dir() if ctx.project else None
    except Exception:
        root = None
    if not root or not root.exists():
        return {"ok": True, "count": 0, "files": [],
                "note": "本项目暂无产物（先执行生成）"}
    exts = (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm", ".mkv", ".mov")
    files = [f for f in root.rglob("*")
             if f.is_file() and f.suffix.lower() in exts]
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return {"ok": True, "count": len(files),
            "files": [{"name": f.name, "path": str(f),
                       "kind": "video" if f.suffix.lower() in
                       (".mp4", ".webm", ".mkv", ".mov") else "image",
                       "mtime": int(f.stat().st_mtime)}
                      for f in files[:10]],
            "note": "可直接把这些文件名填入模板的 image/video 参数（引擎会自动上传）"}


def tool_view_image(ctx: ToolContext, args: dict) -> dict:
    """视觉评估图片（分层：Tier0 免费 + 云端VLM 区域级诊断）。
    args: {paths: [...], use_last?: true, criteria: "要求描述", sample?: 3}
    use_last=true 时评估上一次生成的产物（避免手写路径出错）。
    注意：ok 表示评估工具本身是否成功执行；评估结论在 pass_overall。"""
    if args.get("use_last"):
        last = [p for p in (ctx.draft_meta.get("last_outputs") or [])
                if str(p).lower().endswith((".png", ".jpg", ".jpeg", ".webp"))]
        if not last:
            last = _latest_project_outputs(
                ctx, (".png", ".jpg", ".jpeg", ".webp"))   # 回退：项目内最近图
        if not last:
            return {"ok": False,
                    "error": "没有上一轮产物（先 run_template/submit+fetch）"}
        paths = last
    else:
        paths = args.get("paths", [])
    if not paths:
        return {"ok": False, "error": "缺少 paths（或 use_last=true）"}
    if args.get("use_last"):
        reuse = _engine_eval_reuse(ctx, paths)     # 引擎已评估通过 → 不重复调 VLM
        if reuse:
            return reuse
    criteria = args.get("criteria", "")
    result = evaluate(paths, criteria, sample=args.get("sample"))
    d = result.to_dict()
    verdict = d.pop("ok", None)          # 评估结论（True=通过/False=不达标/None=未知）
    # 评估结论写入轨迹（技能沉淀的成败依据）
    ctx.trajectory.append({"action": "eval", "pass": verdict,
                           "criteria": criteria[:120],
                           "issues": [
                               f"{i.get('location', '')}: {i.get('description', '')}"
                               for v in d.get("vlm", []) or []
                               for i in (v.get("issues") or [])
                               if isinstance(i, dict)][:4]})
    try:
        from .events import emit
        emit("evaluation", {"kind": "image", "verdict": verdict,
                            "score": (d.get("vlm") or [{}])[0].get("score")
                            if d.get("vlm") else None,
                            "issues": [
                                {"location": i.get("location", ""),
                                 "description": i.get("description", "")}
                                for v in d.get("vlm", []) or []
                                for i in (v.get("issues") or [])
                                if isinstance(i, dict)][:4],
                            "files": paths})
    except Exception:
        pass
    return {"ok": True, "pass_overall": verdict, **d}


def tool_inspect_node(ctx: ToolContext, args: dict) -> dict:
    """查节点签名/搜索节点。args: {query}"""
    q = args.get("query", "")
    info = ctx.knowledge.node_info(q)
    if info:
        return {"ok": True, "node": q,
                "inputs": info.get("input"),
                "package": ctx.knowledge.package_of(q)}
    found = ctx.knowledge.find_nodes(q, limit=10)
    return {"ok": True, "query": q, "matches": found}


def tool_edit_workflow(ctx: ToolContext, args: dict) -> dict:
    """增量编辑草稿（版本树式：在上一版基础上改）。
    args: {node_id 或 class_type, set_inputs: {name: value}, remove_node?: bool}
    class_type 匹配第一个该类节点（如 "CLIPTextEncode"）。"""
    if not ctx.draft:
        return {"ok": False, "error": "没有草稿"}
    nid = str(args.get("node_id", ""))
    if nid not in ctx.draft:
        # 按 class_type 定位（容忍大脑记不住内部节点号）
        ct = args.get("class_type", "")
        if ct:
            nid = next((k for k, v in ctx.draft.items()
                        if v.get("class_type") == ct), "")
        if not nid:
            listing = ", ".join(
                f"{k}={v.get('class_type')}" for k, v in ctx.draft.items())
            return {"ok": False,
                    "error": f"节点 {args.get('node_id') or ct} 不存在。"
                             f"可用节点: {listing}"}
    node = ctx.draft[nid]
    if args.get("remove_node"):
        ctx.draft.pop(nid)
        return {"ok": True, "note": f"已删除节点 {nid}"}
    sets = args.get("set_inputs", {})
    node.setdefault("inputs", {}).update(sets)
    # 编辑后即时校验
    issues = validate_workflow(ctx.draft, ctx.knowledge)
    return {"ok": True, "node": nid, "set": sets,
            "validation_issues": [i.to_dict() for i in issues][:8]}


def _upload_candidates(ctx: ToolContext) -> list[Path]:
    """本项目已上传的图片（新→旧）。"""
    try:
        files = [f for f in ctx.project.uploads_dir().glob("*") if f.is_file()]
    except Exception:
        return []
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return files


def _resolve_upload(ctx: ToolContext, raw: str) -> Path | None:
    """图片路径解析：**只认精确匹配**，不做相似度替换。

    曾按 0.75 相似度自动纠错，结果是"抄错一个字符"被静默换成**另一张更早的
    上传图**——用户看着自己的新图被改成了旧图。现在解析不到就返回 None，
    由调用方报错并列出真实候选，让大脑自己改对。
    """
    if not raw:
        return None
    p = Path(raw)
    if p.exists():
        return p
    name = p.name
    for f in _upload_candidates(ctx):
        if f.name == name:
            return f
    return None


def tool_analyze_image(ctx: ToolContext, args: dict) -> dict:
    """分析用户图片（先看再干）：内容/风格/配色/构图+推荐模板与提示词。
    args: {path?: 本地图片路径；不传则分析**本轮用户新上传的那张**}"""
    from .llm import VLMClient, LLMError
    raw = str(args.get("path") or "").strip()
    cur = (ctx.current_upload or {}).get("local_path")
    if not raw and cur:
        p = Path(cur)
        source = "本轮上传"
    else:
        p = _resolve_upload(ctx, raw)
        source = "指定路径"
    if p is None or not Path(p).exists():
        cands = [f.name for f in _upload_candidates(ctx)][:8]
        hint = (f"本项目已上传：{cands}" if cands
                else "本项目还没有上传记录")
        return {"ok": False, "error": f"图片不存在: {raw or cur or '(未提供)'}。{hint}",
                "hint": ("不要改用别的图片。若用户本轮刚上传了图，"
                         "不传 path 直接调用本工具即分析那张；"
                         "若路径抄错，请照上面的真实文件名重试一次")}
    try:
        vlm = VLMClient()
        if not vlm.ready:
            return {"ok": False, "error": "VLM 未配置，无法看图",
                    "hint": ("如实告诉用户你看不到这张图（视觉模型未配置），"
                             "请其用文字描述或配置 ⚙ 里的视觉模型；"
                             "禁止编造图片内容或改用其它图片")}
        analysis = vlm.analyze_image_json(p)
        return {"ok": True, "path": str(p), "source": source, **analysis}
    except LLMError as e:
        msg = str(e)
        if "contentFilter" in msg or "1301" in msg:
            return {"ok": False,
                    "error": "图片被服务商内容安全策略拦截，视觉模型无法分析。"
                             "可改用文字描述画面内容，或更换图片后重试。",
                    "hint": ("如实告诉用户这张图被内容安全策略拦截、你看不到；"
                             "不要编造画面内容，也不要拿别的图顶替")}
        return {"ok": False, "error": msg[:200],
                "hint": ("如实告诉用户看图失败（附上错误原因），"
                         "请其用文字描述或改配置；禁止编造图片内容、"
                         "禁止改用项目里的其它图片")}


def tool_run_template(ctx: ToolContext, args: dict) -> dict:
    """模板快捷执行（渲染+五段管线），结果与 run_workflow 同构。
    执行后草稿与产物关联，供 edit_workflow 增量修改。"""
    tid = args.get("template_id", "")
    params = args.get("params", {})
    tpl = get_template(tid)
    if tpl is not None:
        try:
            ctx.draft = tpl.render(params)
            ctx.draft_meta = {"template_id": tid, "params": params,
                              "family": tpl.family}
        except Exception:
            pass
    result = run_template(tid, params, client=ctx.client,
                          knowledge=ctx.knowledge,
                          output_root=ctx.project.outputs_dir()
                          if ctx.project else None,
                          current_upload=ctx.current_upload)
    if result.get("ok"):
        ctx.prompt_id = result.get("prompt_id")
        ctx.draft_meta["last_outputs"] = [
            o.get("local_path") for o in result.get("outputs", [])
            if o.get("local_path")]
        result["server_images"] = [
            o.get("filename") for o in result.get("outputs", [])
            if o.get("filename")]
        # 引擎已在 run_workflow 末尾做过强制评估：记下来供 view_image/view_video
        # 复用（实测引擎 9/10 通过、大脑自评 6/10 → 无谓重生成 3 段视频）
        ev_res = result.get("evaluation") or {}
        if ev_res and "error" not in ev_res:
            ctx.draft_meta["last_eval"] = {
                "prompt_id": result.get("prompt_id"),
                "verdict": ev_res.get("verdict"),
                "score": (ev_res.get("vlm") or [{}])[0].get("score")
                if ev_res.get("vlm") else None,
                "files": [str(p) for p in ctx.draft_meta["last_outputs"]],
            }
        ctx.trajectory.append({"action": "run", "template": tid,
                               "ok": True, "params": params})
    else:
        ctx.trajectory.append({"action": "run", "template": tid,
                               "ok": False, "note": str(result.get("hint", ""))[:150]})
    return result


def tool_view_video(ctx: ToolContext, args: dict) -> dict:
    """视频评估：ffmpeg 抽帧 + 逐帧 Tier0/VLM + 聚合判定。
    args: {use_last?: true, path?: "本地视频路径", criteria: "要求", frames?: 4}"""
    if args.get("use_last"):
        last = ctx.draft_meta.get("last_outputs") or []
        vids = [p for p in last
                if str(p).lower().endswith((".mp4", ".webm", ".mkv", ".mov"))]
        if not vids:
            # 回退：扫项目产物目录里最近的视频（实测中间失败会清掉 last_outputs）
            vids = _latest_project_outputs(
                ctx, (".mp4", ".webm", ".mkv", ".mov"))
        if vids:
            reuse = _engine_eval_reuse(ctx, vids)
            if reuse:
                return reuse
        if not vids:
            return {"ok": False,
                    "error": "上一轮产物中没有视频（先跑视频模板）"}
        path = vids[0]
    else:
        path = args.get("path", "")
    if not path:
        return {"ok": False, "error": "缺少视频路径（或 use_last=true）"}
    criteria = args.get("criteria", "")
    result = evaluate_video(path, criteria, frames=int(args.get("frames", 4)))
    d = result.to_dict()
    verdict = d.pop("ok", None)
    ctx.trajectory.append({"action": "eval_video", "pass": verdict,
                           "video": path[:100],
                           "criteria": criteria[:120]})
    try:
        from .events import emit
        emit("evaluation", {"kind": "video", "verdict": verdict,
                            "score": (d.get("vlm") or [{}])[0].get("score")
                            if d.get("vlm") else None,
                            "issues": [
                                {"location": f"第{i.get('frame_index', 0)+1}帧",
                                 "description": "；".join(
                                     x.get("description", "")
                                     for x in (i.get("issues") or [])
                                     if isinstance(x, dict))[:120]}
                                for i in d.get("vlm", []) or []
                                if i.get("pass") is False][:4],
                            "files": [path]})
    except Exception:
        pass
    return {"ok": True, "pass_overall": verdict, **d}


def tool_learn_node(ctx: ToolContext, args: dict) -> dict:
    """学习节点语义档案（LLM 按需生成+缓存）：中文用途/输入接线/输出/坑。
    args: {class_type}"""
    from .llm import LLMClient, LLMError
    from .node_profiles import learn, format_profile
    cls = args.get("class_type", "")
    if not ctx.knowledge.has_node(cls):
        return {"ok": False,
                "error": f"本机没有节点 {cls}（用 search_nodes 找正确的）"}
    try:
        entry = learn(cls, ctx.knowledge, LLMClient())
        return {"ok": True, "profile": format_profile(entry)}
    except LLMError as e:
        return {"ok": False, "error": str(e)[:200]}


def tool_search_nodes(ctx: ToolContext, args: dict) -> dict:
    """语义搜索节点（支持中文概念：放大/采样/视频/人脸...）。
    args: {goal, category?}"""
    goal = args.get("goal", "")
    if not goal:
        return {"ok": False, "error": "缺少 goal"}
    hits = ctx.knowledge.find_nodes(goal, limit=10,
                                    category=args.get("category"))
    return {"ok": True, "matches": hits,
            "note": "对候选节点用 learn_node 看详情再接线"}


def tool_scaffold(ctx: ToolContext, args: dict) -> dict:
    """空图搭骨架（从零建图起点）。args: {kind: "t2i"|"i2i"}"""
    from comfy_agent.synth.scaffolds import scaffold
    from comfy_agent.synth.validate_graph import validate_graph
    kind = args.get("kind", "t2i")
    try:
        g = scaffold(ctx.knowledge, kind)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    issues = validate_graph(g)
    blockers = [i for i in issues if i["reason"] not in ("orphan_output",)]
    ctx.draft = g.to_api()
    ctx.draft_meta["scaffold"] = kind
    return {"ok": True, "kind": kind, "nodes": len(g.nodes()),
            "blockers": blockers,
            "note": "骨架已入草稿。用 propose_edit 继续加环节；"
                    "不认识的节点先 learn_node"}


def tool_run_workflow(ctx: ToolContext, args: dict) -> dict:
    """主执行工具：把草稿（或显式 workflow JSON）走统一五段管线执行。
    返回 {stage: completed|validation_failed|repair_failed|execution_failed, ...}
    stage=failed 时读 issues/unfixable/suggestion 做语义修复后重跑。"""
    workflow = args.get("workflow")
    if workflow is None:
        if not ctx.draft:
            return {"ok": False, "error": "没有草稿也没有 workflow（先 render/"
                                            "compose/synthesize/load_workflow）"}
        workflow = ctx.draft
    source = args.get("source", ctx.draft_meta.get("pipeline") and
                      "->".join(ctx.draft_meta["pipeline"]) or "workflow")
    result = run_workflow(workflow, source=source, client=ctx.client,
                          knowledge=ctx.knowledge,
                          output_root=ctx.project.outputs_dir()
                          if ctx.project else None)
    # 轨迹与产物关联
    if result.get("ok"):
        ctx.prompt_id = result.get("prompt_id")
        ctx.draft_meta["last_outputs"] = [
            o.get("local_path") for o in result.get("outputs", [])
            if o.get("local_path")]
        result["server_images"] = [
            o.get("filename") for o in result.get("outputs", [])
            if o.get("filename")]
        ctx.trajectory.append({"action": "run", "template": result.get("source"),
                               "ok": True})
    else:
        ctx.trajectory.append({"action": "run", "template": source, "ok": False,
                               "note": f"stage={result.get('stage')}: "
                                       f"{str(result.get('hint', ''))[:150]}"})
    return result


def tool_load_workflow(ctx: ToolContext, args: dict) -> dict:
    """加载任意本地工作流文件（blueprints/用户工作流/UI格式JSON）
    → UI→API 转换 → 本地校验报告 → 存草稿。
    args: {path}"""
    from comfy_agent.convert import convert_file, ConversionError
    p = Path(args.get("path", ""))
    if not p.exists():
        return {"ok": False, "error": f"文件不存在: {p}"}
    try:
        api = convert_file(p, ctx.knowledge)
    except ConversionError as e:
        return {"ok": False, "error": f"转换失败: {str(e)[:300]}"}
    ctx.draft = api
    ctx.draft_meta = {"source_file": str(p)}
    issues = validate_workflow(api, ctx.knowledge)
    blockers = [i.to_dict() for i in issues
                if not (i.input_name and i.input_name.lower() == "image"
                        and i.kind in ("bad_enum", "missing_file"))]
    return {"ok": True, "nodes": len(api), "source": str(p),
            "validation_issues": blockers,
            "note": "工作流已入草稿。有 blocker 先修（edit_workflow），"
                    "通过后 run_workflow 执行"}


def tool_search_nodes(ctx: ToolContext, args: dict) -> dict:
    """语义搜索节点：关键词候选 top-10 → LLM 精排（结合 L0 索引一行用途）。
    支持中文模糊表达（"让图变清晰"）。args: {goal, category?, top?}"""
    from .llm import LLMClient, LLMError
    goal = args.get("goal", "")
    if not goal:
        return {"ok": False, "error": "缺少 goal"}
    top = int(args.get("top", 5))
    cands = ctx.knowledge.find_nodes(goal, limit=10,
                                     category=args.get("category"))
    try:
        llm = LLMClient()
    except Exception:
        llm = None
    # 候选太薄 → LLM 概念翻译：中文需求 → 英文节点名关键词 → 再搜
    if len(cands) < 3 and llm is not None and llm.ready:
        try:
            raw = llm.chat([{"role": "user", "content":
                             f"把需求「{goal}」翻译成 3-6 个 ComfyUI 节点名的"
                             f"英文关键词，逗号分隔，只输出关键词"}],
                           temperature=0.1, max_tokens=80, thinking=False)
            extra = [w.strip() for w in raw.replace("\n", ",").split(",")
                     if w.strip()]
            seen = {c["class"] for c in cands}
            for kw in extra:
                for c in ctx.knowledge.find_nodes(kw, limit=6):
                    if c["class"] not in seen:
                        seen.add(c["class"])
                        cands.append(c)
        except Exception:
            pass
    if not cands:
        return {"ok": False, "error": f"没找到与「{goal}」相关的节点"}
    # LLM 精排（结合 L0 一行用途）
    try:
        if llm is not None and llm.ready:
            options = "\n".join(
                f"{i+1}. {c['class']}｜{c.get('display_name', '')}｜"
                f"{c.get('category', '')}" for i, c in enumerate(cands))
            prompt = (f"用户想找节点用于：{goal}\n候选（关键词初筛）：\n{options}\n"
                      f"按语义匹配度排序，只输出最相关的 {top} 个编号"
                      f"（JSON数组，如 [3, 1, 7]）")
            raw = llm.chat([{"role": "user", "content": prompt}],
                           temperature=0.1, max_tokens=100, thinking=False)
            nums = _parse_rankings(raw, len(cands))
            ranked = [cands[i - 1] for i in nums if 1 <= i <= len(cands)][:top]
        else:
            ranked = cands[:top]
    except Exception:
        ranked = cands[:top]
    # 附 L0 一行用途
    from .node_profiles import ProfileStore
    store = ProfileStore()
    for c in ranked:
        prof = store.get(c["class"])
        if prof:
            c["one_line"] = prof.get("one_line") or prof.get("purpose", "")[:40]
    return {"ok": True, "goal": goal, "matches": ranked,
            "note": "对候选节点用 learn_node 看完整档案再接线"}


def _parse_rankings(raw: str, n: int) -> list[int]:
    from .llm import _extract_json
    d = _extract_json(raw)
    arr = None
    for k, v in d.items():
        if isinstance(v, list) and v and isinstance(v[0], (int, float)):
            arr = v
            break
    if arr is None:
        import re
        arr = [int(x) for x in re.findall(r"\d+", raw)][:n]
    return [int(x) for x in arr][:n]


def tool_read_skill(ctx: ToolContext, args: dict) -> dict:
    """按需读家族技能文档（渐进披露：默认不进上下文）。
    args: {name: 文件名如 "sampling" 或中文名如 "采样"}"""
    from pathlib import Path
    FAMILY_NAME_MAP = {
        "模型加载": "model_loading", "条件": "conditioning", "采样": "sampling",
        "潜空间": "latent", "图像": "image_io", "图像处理": "image_process",
        "放大": "upscale", "controlnet": "controlnet", "视频": "video",
        "音频": "audio", "遮罩": "mask", "检测": "detection",
        "逻辑": "logic", "修复": "inpaint", "其他": "other",
    }
    name = str(args.get("name", "")).strip()
    skills_dir = Path(__file__).parent / "skills"
    # 名称映射：中文家族名 → 文件 stem；其余直接当文件名
    fid = FAMILY_NAME_MAP.get(name, name)
    # 路径安全：只允许 skills 目录内的 .md
    candidate = skills_dir / f"{fid}.md"
    if not candidate.exists():
        candidate = skills_dir / "families" / f"{fid}.md"
    if not candidate.exists() or \
            not str(candidate.resolve()).startswith(str(skills_dir.resolve())):
        available = sorted(f.stem for f in skills_dir.glob("*.md")) + \
            sorted(f.stem for f in (skills_dir / "families").glob("*.md"))
        return {"ok": False,
                "error": f"技能 {name} 不存在。可用: {available}"}
    return {"ok": True, "skill": candidate.read_text(encoding="utf-8")[:2500]}


def tool_ask_user(ctx: ToolContext, args: dict) -> dict:
    """向用户提问（计划确认/澄清）。args: {question}"""
    q = args.get("question", "")
    if ctx.ask_user_fn is None:
        return {"ok": False,
                "error": "当前为非交互模式，无法提问。请基于现有信息决策。"}
    answer = ctx.ask_user_fn(q)
    return {"ok": True, "answer": answer}


# ---------------- 图合成工具 ----------------

def tool_compose(ctx: ToolContext, args: dict) -> dict:
    """确定性管线拼接：把多个模板在类型化端口串联。
    args: {steps: [{"template_id": "t2i", "params": {...}}, ...]}
    也接受纯 id 列表 ["t2i", "upscale_pass"]（参数用 params_per_step）"""
    from comfy_agent.synth.compose import compose
    from comfy_agent.synth.graph import Graph
    from comfy_agent.synth.validate_graph import validate_graph
    raw = args.get("steps", [])
    if len(raw) < 2:
        return {"ok": False, "error": "steps 至少两个模板"}
    # 归一化：支持字符串 id 或 {template_id, params} 对象两种形式
    steps: list[tuple[str, dict]] = []
    for item in raw:
        if isinstance(item, str):
            params = (args.get("params_per_step") or {}).get(item, {})
            steps.append((item, params))
        elif isinstance(item, dict) and item.get("template_id"):
            steps.append((item["template_id"], item.get("params", {})))
        else:
            return {"ok": False, "error": f"steps 项格式无效: {item!r}"}
    try:
        merged = None
        for tid, params in steps:
            tpl = get_template(tid)
            if tpl is None:
                return {"ok": False, "error": f"未知模板 {tid}"}
            # 管线中 image 参数由拼接自动接管（上游产出替换 LoadImage），
            # 渲染前注入占位避免"必填 image"误伤
            if any(prm.name == "image" for prm in tpl.params()) and \
                    not params.get("image"):
                params = {**params, "image": "__compose_placeholder__"}
            api = tpl.render(params)
            merged = api if merged is None else compose(merged, api, ctx.knowledge)
        g = Graph(merged, ctx.knowledge)
        blockers = [i for i in validate_graph(g)
                    if i["reason"] not in ("orphan_output",)]
        ctx.draft = merged
        ctx.draft_meta = {"pipeline": [tid for tid, _ in steps]}
        return {"ok": True, "pipeline": [tid for tid, _ in steps],
                "nodes": len(merged), "blockers": blockers,
                "note": "拼接完成已存草稿，submit 即可执行"}
    except Exception as e:
        return {"ok": False, "error": f"拼接失败: {e}"}


def tool_synthesize(ctx: ToolContext, args: dict) -> dict:
    """开合成会话：goal 描述目标；base_template 用现有模板做底，
    或 scaffold="t2i"/"i2i" 从骨架起家，两者都不给则完全空图。"""
    from comfy_agent.synth.builder import SynthSession
    from comfy_agent.synth.scaffolds import scaffold as make_scaffold
    goal = args.get("goal", "")
    if not goal:
        return {"ok": False, "error": "缺少 goal"}
    base = None
    base_tpl = args.get("base_template")
    if base_tpl:
        tpl = get_template(base_tpl)
        if tpl is None:
            return {"ok": False, "error": f"未知模板 {base_tpl}"}
        base = tpl.render(args.get("base_params", {}))
    elif args.get("scaffold"):
        try:
            base = make_scaffold(ctx.knowledge, args["scaffold"]).to_api()
        except ValueError as e:
            return {"ok": False, "error": str(e)}
    ctx.synth = SynthSession(goal, ctx.knowledge, base_api=base)
    return {"ok": True, "summary": ctx.synth.summary(),
            "note": "用小步编辑构建：每步先 inspect_node/learn_node 查签名，"
                    "再 propose_edit 提交（失败会回滚并给诊断）"}


def tool_propose_edit(ctx: ToolContext, args: dict) -> dict:
    """提交一批图编辑 op（原子应用，失败整体回滚）。
    args: {edits: [{op, ...}]}；op ∈ add_node/set_input/connect/disconnect/
    remove_node/insert_lora/insert_controlnet"""
    if ctx.synth is None:
        return {"ok": False, "error": "没有合成会话（先 synthesize）"}
    edits = args.get("edits", [])
    if not edits:
        return {"ok": False, "error": "缺少 edits"}
    result = ctx.synth.propose(edits)
    if result.get("accepted"):
        ctx.draft = ctx.synth.to_api()
        ctx.draft_meta["synth_goal"] = ctx.synth.goal
    return {"ok": True, **result, "summary": ctx.synth.summary()}


# ---------------- 缺模型搜索 / 下载 ----------------

def _free_vram_gb(ctx: ToolContext) -> float | None:
    """当前空闲显存（GB），取不到返回 None（仅用于适配建议）。"""
    try:
        stats = ctx.client.system_stats()
        best = 0
        for d in stats.get("devices", []):
            best = max(best, d.get("vram_free", 0))
        return round(best / 1e9, 1) if best else None
    except Exception:
        return None


def _retry_context(ctx: ToolContext, args: dict) -> dict:
    """下载成功后要自动重跑的任务描述（优先工具入参，否则用当前草稿）。"""
    tid = args.get("retry_template") or ctx.draft_meta.get("template_id")
    if not tid:
        return {}
    return {"template": tid,
            "params": args.get("retry_params")
            or ctx.draft_meta.get("params") or {}}


def tool_search_models(ctx: ToolContext, args: dict) -> dict:
    """搜索缺失模型的可下载来源（只读，不落盘不弹窗）。
    args: {filename: "缺哪个文件", folder?: "models 子目录",
           offline?: true 只用本机 Manager 目录}"""
    from comfy_agent import model_download as md
    filename = str(args.get("filename") or args.get("name") or "").strip()
    if not filename:
        return {"ok": False, "error": "缺少 filename（要下载的模型文件名）"}
    folder = args.get("folder") or None
    try:
        cands = md.search_models(filename, folder=folder,
                                 vram_free_gb=_free_vram_gb(ctx),
                                 offline=bool(args.get("offline")),
                                 limit=int(args.get("limit") or 8))
    except Exception as e:
        return {"ok": False, "error": f"搜索失败：{type(e).__name__}: {e}",
                "hint": "搜索失败按缺模型降级处理，不要反复重试同一来源"}
    if not cands:
        return {"ok": True, "query": filename, "count": 0, "candidates": [],
                "hint": ("没有找到可下载来源。按缺模型降级：换用不需要该模型的"
                         "等价模板，或告知用户需自行安装该文件")}
    brief = [{"filename": c["filename"], "folder": c["folder"],
              "source": c["source"], "size_text": c["size_text"],
              "fits": c["fit"]["fits"], "target_dir": c["target_dir"],
              "url": c["url"], "name": c["name"]} for c in cands]
    # 记住候选：download_model 会据此校正大脑自拟/猜错的 url 与大小
    # （实测大脑会把 4.71MB 的候选写成 1.2GB + 自己拼一个不存在的 HF 地址）
    cache = ctx.draft_meta.setdefault("_model_candidates", {})
    for c in cands:
        cache.setdefault(c["filename"].lower(), []).append(c)
    return {"ok": True, "query": filename, "count": len(brief),
            "candidates": brief, "vram_free_gb": _free_vram_gb(ctx),
            "hint": ("取第一条候选（已按匹配度排序）调用 download_model，"
                     "url/filename/folder/size 一律原样照抄候选字段："
                     "url 不要自己拼（拼出来的地址实测 404），"
                     "size 用候选的 size_text，不要自己估算。"
                     "弹窗会显示名称/大小/来源/是否适配/目标目录，由用户决定是否下载。")}


def _pick_cached_candidate(ctx: ToolContext, args: dict) -> dict | None:
    """从上次 search_models 的候选里挑同文件同目录的一条（用于校正 url/大小）。"""
    cache = (ctx.draft_meta or {}).get("_model_candidates") or {}
    want = str(args.get("filename") or "").strip().lower()
    if not want:
        return None
    cands = cache.get(want) or []
    folder = str(args.get("folder") or "").strip()
    for c in cands:
        if not folder or c.get("folder") == folder:
            return c
    return cands[0] if cands else None


def tool_download_model(ctx: ToolContext, args: dict) -> dict:
    """请求下载缺失模型：登记后立刻返回，等用户在弹窗里确认。
    args: {url, filename, folder, size?, source?, retry_template?,
           retry_params?}"""
    from comfy_agent import model_download as md
    miss = [k for k in ("url", "filename", "folder") if not args.get(k)]
    if miss:
        return {"ok": False, "error": f"缺少必填参数 {miss}"}
    url = str(args["url"])
    size = md.parse_size(args.get("size") or args.get("size_bytes"))
    source = str(args.get("source") or "")
    substituted = False
    cand = _pick_cached_candidate(ctx, args)
    if cand is not None:
        # 候选来自权威目录（含真实 url/大小/来源）：大脑给的与候选不一致时以候选为准
        if cand.get("url") and cand["url"] != url:
            url = cand["url"]
            substituted = True
        if substituted or not source:
            source = str(cand.get("source") or source)
        if cand.get("size"):
            size = int(cand["size"])
    try:
        rec = md.MANAGER.request(
            url, str(args["filename"]), str(args["folder"]),
            source=source, size=size, project_id=ctx.project_id,
            retry=_retry_context(ctx, args),
            note=str(args.get("note") or ""),
            name=str(args.get("name") or (cand or {}).get("name") or ""))
    except md.DownloadError as e:
        return {"ok": False, "error": str(e),
                "hint": ("该候选不可用（地址或落盘路径未通过安全校验/超出上限）。"
                         "换 search_models 返回的其它候选，或按缺模型降级")}
    out = {"ok": True, "state": rec["state"], "download_id": rec["id"],
           "filename": rec["filename"], "size_text": rec["size_text"],
           "source": rec["source"], "dest": rec["dest"],
           "fits": rec["fit"]["fits"],
           "fit_notes": list(rec["fit"]["notes"]) + list(rec.get("warnings") or []),
           "awaiting_confirm": True,
           "note": ("下载确认弹窗已弹出，等待用户决定——不要重复调用本工具、"
                    "不要自行判断用户是否同意。用户确认后系统会自动重跑刚才"
                    "失败的任务；用户拒绝或下载失败时，按缺模型降级继续。")}
    if substituted:
        out["url_corrected"] = True
        out["note"] = ("你给的下载地址与 search_models 的权威候选不一致，"
                       "已改用候选地址。 " + out["note"])
    return out


# ---------------- 注册表 ----------------

TOOLS: dict[str, dict] = {
    "list_templates": {"fn": tool_list_templates,
                       "desc": "列出可用模板（含参数）",
                       "args": {}},
    "list_outputs": {"fn": tool_list_outputs,
                     "desc": "列出本项目最近的产物文件（填进模板 image/video 参数用；重启后不要猜路径）",
                     "args": {}},
    "render_workflow": {"fn": tool_render_workflow,
                         "desc": "渲染模板为工作流草稿并本地校验",
                         "args": {"template_id": "str", "params": "dict"}},
    "run_template": {"fn": tool_run_template,
                     "desc": "一步执行模板（渲染→修复→提交→等待→下载）",
                     "args": {"template_id": "str", "params": "dict"}},
    "list_models": {"fn": tool_list_models,
                    "desc": "本机模型清单", "args": {"folder?": "str"}},
    "upload_image": {"fn": tool_upload_image,
                     "desc": "上传用户图片到 /input",
                     "args": {"path": "str"}},
    "validate": {"fn": tool_validate,
                 "desc": "本地校验草稿（不提交不花钱）", "args": {}},
    "submit": {"fn": tool_submit,
               "desc": "提交草稿（校验+自动修复+入队）", "args": {}},
    "wait_result": {"fn": tool_wait_result,
                    "desc": "等待任务完成", "args": {"timeout_sec?": "int"}},
    "fetch_outputs": {"fn": tool_fetch_outputs,
                      "desc": "下载执行产物", "args": {}},
    "view_image": {"fn": tool_view_image,
                   "desc": "视觉评估图片（Tier0+VLM 区域级诊断）。"
                           "评估上一次产物用 {\"use_last\": true}，"
                           "不要手写路径",
                   "args": {"use_last?": "bool", "paths?": "list",
                            "criteria?": "str", "sample?": "int"}},
    "analyze_image": {"fn": tool_analyze_image,
                      "desc": "分析用户图片：内容/风格/配色/构图 + 推荐模板与提示词",
                      "args": {"path": "str"}},
    "inspect_node": {"fn": tool_inspect_node,
                     "desc": "查节点签名/模糊搜索节点", "args": {"query": "str"}},
    "edit_workflow": {"fn": tool_edit_workflow,
                      "desc": "增量编辑草稿节点参数（用 class_type 定位，如 CLIPTextEncode 是提示词节点）",
                      "args": {"class_type": "str", "set_inputs": "dict"}},
    "ask_user": {"fn": tool_ask_user,
                 "desc": "向用户提问（澄清/确认）", "args": {"question": "str"}},
    "compose": {"fn": tool_compose,
                "desc": "管线拼接：把多个模板串联成一条流水线执行"
                        "（如 文生图→高清放大）。steps 每项可为模板id字符串"
                        "或 {\"template_id\":..., \"params\":{...}}",
                "args": {"steps": "list"}},
    "synthesize": {"fn": tool_synthesize,
                   "desc": "开图合成会话（目标+可选基础模板），返回图摘要供小步编辑",
                   "args": {"goal": "str", "base_template?": "str",
                            "base_params?": "dict"}},
    "propose_edit": {"fn": tool_propose_edit,
                     "desc": "提交图编辑批量（原子应用，失败回滚+诊断）",
                     "args": {"edits": "list"}},
    "view_video": {"fn": tool_view_video,
                   "desc": "视频评估（ffmpeg抽帧+逐帧VLM+聚合判定）。"
                           "评估上一次视频产物用 {\"use_last\": true}",
                   "args": {"use_last?": "bool", "path?": "str",
                            "criteria?": "str", "frames?": "int"}},
    "learn_node": {"fn": tool_learn_node,
                   "desc": "学习节点语义档案（用途/接线/坑，LLM生成+缓存，新节点也能学）",
                   "args": {"class_type": "str"}},
    "search_nodes": {"fn": tool_search_nodes,
                     "desc": "语义搜索节点（支持中文概念：放大/采样/视频/人脸）",
                     "args": {"goal": "str", "category?": "str"}},
    "scaffold": {"fn": tool_scaffold,
                  "desc": "空图搭标准骨架（从零建图起点，t2i或i2i）",
                  "args": {"kind": "str"}},
    "run_workflow": {"fn": tool_run_workflow,
                     "desc": "主执行工具：草稿走统一五段管线执行。"
                             "stage=failed 时读 issues/suggestion 修复后重跑",
                     "args": {"workflow?": "dict", "source?": "str"}},
    "load_workflow": {"fn": tool_load_workflow,
                      "desc": "加载任意本地工作流文件→转换→校验报告→草稿",
                      "args": {"path": "str"}},
    "read_skill": {"fn": tool_read_skill,
                   "desc": "按需读家族技能文档（节点家族接线方法论）",
                   "args": {"name": "str"}},
    "search_models": {"fn": tool_search_models,
                      "desc": "搜索缺失模型的可下载来源（本机 Manager 目录 + "
                              "HF/hf-mirror/Civitai/ModelScope），返回大小/是否"
                              "适配本机/应存放的目录",
                      "args": {"filename": "str", "folder?": "str",
                               "offline?": "bool"}},
    "download_model": {"fn": tool_download_model,
                       "desc": "请求下载缺失模型：弹出确认弹窗让用户决定，"
                               "立刻返回不阻塞；用户同意后自动下载并重跑失败任务",
                       "args": {"url": "str", "filename": "str",
                                "folder": "str", "size?": "str",
                                "source?": "str", "retry_template?": "str",
                                "retry_params?": "dict"}},
}


def tools_schema_for_llm() -> str:
    """生成给 LLM 的工具说明。"""
    lines = []
    for name, t in TOOLS.items():
        args = ", ".join(f"{k}:{v}" for k, v in t["args"].items()) or "无"
        lines.append(f"- {name}({args}): {t['desc']}")
    return "\n".join(lines)


def execute_tool(ctx: ToolContext, name: str, args: dict) -> dict:
    t = TOOLS.get(name)
    if t is None:
        return {"ok": False, "error": f"未知工具 {name}"}
    try:
        return t["fn"](ctx, args or {})
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
