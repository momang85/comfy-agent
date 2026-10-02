# -*- coding: utf-8 -*-
"""comfy-agent 执行引擎的 MCP server（stdio，零第三方依赖）。

让 Claude Desktop / Cursor / Claude Code 等 MCP 客户端把本机 ComfyUI 的
「校验 → 修复 → 执行 → 下载」闭环当工具用：客户端自带的 LLM 当大脑，
本模块只暴露引擎能力，绝不 import brain/（大脑由 MCP 客户端自己扮演）。

协议：MCP stdio = 每行一条 JSON-RPC 2.0 消息（UTF-8，无 Content-Length 帧）。
stdout 只走协议；工具执行期间引擎可能产生的 stdout 输出被重定向到 stderr，
防止污染协议帧。日志一律走 stderr。

用法：python -m comfy_agent.mcp_server   （或 scripts/mcp_server.py 启动器）
"""
from __future__ import annotations

import io
import json
import sys
import traceback

SERVER_INFO = {"name": "comfy-agent-mcp", "version": "0.3.0"}
SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

INSTRUCTIONS = (
    "Tools drive a local ComfyUI (127.0.0.1:8188) through comfy-agent's "
    "validated execution pipeline: render template → local validation → "
    "auto-repair → submit → download. Call comfy_inspect first to learn "
    "what nodes/models exist on this machine, comfy_list_templates for "
    "runnable templates, then comfy_run_template. Image renders typically "
    "take 15-90s; keep wait=true for the full validated result."
)


# ---------------------------------------------------------------- 工具清单

def _tool_specs() -> list[dict]:
    return [
        {
            "name": "comfy_status",
            "description": "本机 ComfyUI 服务器状态（版本/GPU/显存）。不可达时返回 ok=false 与启动提示。",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": True},
        },
        {
            "name": "comfy_inspect",
            "description": "本机能力摘要：已装节点/模型/模板适配情况（给 LLM 看的一段文字）。决定怎么生成图之前先调这个。",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": True},
        },
        {
            "name": "comfy_list_templates",
            "description": "可执行模板目录：id、名称、参数表（类型/默认值/说明）。",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": True},
        },
        {
            "name": "comfy_run_template",
            "description": "执行一个模板（走完整管线：本地校验→自动修复→提交→下载）。返回统一结果：stage/ok/outputs/repairs/validation_issues。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "template_id": {"type": "string", "description": "模板 id（先 comfy_list_templates 查）"},
                    "params": {"type": "object", "description": "模板参数（prompt/width/height/image 等，按参数表给）"},
                    "criteria": {"type": "string", "description": "可选。本轮验收判据（自然语言），会用于强制评估"},
                    "wait": {"type": "boolean", "description": "等待执行完成（默认 true；false 时入队即返回）"},
                },
                "required": ["template_id"],
            },
        },
        {
            "name": "comfy_run_workflow",
            "description": "执行任意工作流文件（UI 格式自动转 API 格式；API 格式直接跑），走同一条校验-修复管线。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "工作流 JSON 的本机绝对路径"},
                    "criteria": {"type": "string", "description": "可选。验收判据"},
                },
                "required": ["path"],
            },
        },
        {
            "name": "comfy_upload_image",
            "description": "把本机图片上传到 ComfyUI /input，返回 server_name（供模板的 image 参数使用）。",
            "inputSchema": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "图片绝对路径"}},
                "required": ["path"],
            },
        },
        {
            "name": "comfy_models",
            "description": "列出本机模型（可按 folder 过滤，如 checkpoints/loras/vae）。",
            "inputSchema": {
                "type": "object",
                "properties": {"folder": {"type": "string", "description": "可选。模型目录名"}},
            },
        },
    ]


# ---------------------------------------------------------------- 工具实现

def _safe_read_path(raw: str):
    # 与 CLI 同一条路径安全规则（允许根：项目/ComfyUI/桌面/下载）
    from .cli import _safe_read_path
    return _safe_read_path(raw)


def _h_status(args: dict) -> dict:
    from .client import Client
    try:
        stats = Client().system_stats()
    except Exception as e:
        return {"ok": False,
                "error": f"ComfyUI 不可达：{e}",
                "hint": "先启动 ComfyUI（默认 http://127.0.0.1:8188）",
                "connected": False}
    return {"ok": True, "connected": True, "stats": stats}


def _h_inspect(args: dict) -> dict:
    from .knowledge import Knowledge
    return {"ok": True, "summary": Knowledge.build().summary_for_llm()}


def _h_list_templates(args: dict) -> dict:
    from .templates import catalog
    return {"ok": True, "templates": catalog()}


def _h_run_template(args: dict) -> dict:
    from .runner import run_template
    template_id = str(args.get("template_id") or "").strip()
    params = args.get("params") or {}
    if not isinstance(params, dict):
        return {"ok": False, "error": "params 必须是对象"}
    kw = {}
    if args.get("criteria"):
        kw["criteria"] = str(args["criteria"])
    if "wait" in args:
        kw["wait"] = bool(args["wait"])
    return run_template(template_id, params, **kw)


def _h_run_workflow(args: dict) -> dict:
    from .runner import run_workflow
    try:
        src = _safe_read_path(str(args.get("path") or ""))
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except Exception as e:
        return {"ok": False, "error": f"工作流 JSON 解析失败：{e}"}
    kw = {}
    if args.get("criteria"):
        kw["criteria"] = str(args["criteria"])
    # UI 格式（nodes/links 数组）→ 转 API 格式；API 格式直接跑
    if isinstance(data, dict) and isinstance(data.get("nodes"), list):
        from .knowledge import Knowledge
        from .convert import convert_file
        try:
            data = convert_file(src, Knowledge.build())
        except Exception as e:
            return {"ok": False, "error": f"UI→API 转换失败：{e}"}
    if not (isinstance(data, dict) and data
            and all(isinstance(v, dict) and "class_type" in v for v in data.values())):
        return {"ok": False,
                "error": "不是可识别的工作流格式（需要 UI 格式或 API 格式的 JSON）"}
    return run_workflow(data, source=src.name, **kw)


def _h_upload_image(args: dict) -> dict:
    from .runner import upload_input_image
    try:
        src = _safe_read_path(str(args.get("path") or ""))
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    try:
        return {"ok": True, "server_name": upload_input_image(src)}
    except Exception as e:
        return {"ok": False, "error": f"上传失败：{e}"}


def _h_models(args: dict) -> dict:
    from .client import Client
    folder = args.get("folder")
    try:
        return {"ok": True, "models": Client().models(folder)}
    except Exception as e:
        return {"ok": False, "error": f"ComfyUI 不可达：{e}",
                "hint": "先启动 ComfyUI（默认 http://127.0.0.1:8188）"}


TOOL_HANDLERS = {
    "comfy_status": _h_status,
    "comfy_inspect": _h_inspect,
    "comfy_list_templates": _h_list_templates,
    "comfy_run_template": _h_run_template,
    "comfy_run_workflow": _h_run_workflow,
    "comfy_upload_image": _h_upload_image,
    "comfy_models": _h_models,
}


# ---------------------------------------------------------------- 协议层

def _result(id_, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _error(id_, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_,
            "error": {"code": code, "message": message}}


def handle_message(msg: dict) -> dict | None:
    """处理一条已解析的 JSON-RPC 请求；通知返回 None。"""
    method = msg.get("method") or ""
    id_ = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        requested = str(params.get("protocolVersion") or "")
        version = requested if requested in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[-1]
        return _result(id_, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": INSTRUCTIONS,
        })
    if method == "ping":
        return _result(id_, {})
    if method == "tools/list":
        return _result(id_, {"tools": _tool_specs()})
    if method == "tools/call":
        name = str(params.get("name") or "")
        args = params.get("arguments") or {}
        handler = TOOL_HANDLERS.get(name)
        if handler is None:
            return _error(id_, -32602, f"未知工具 {name!r}；可用：{', '.join(TOOL_HANDLERS)}")
        # 引擎侧任何 print 都会污染协议帧：执行期把 stdout 指到 stderr
        real_stdout = sys.stdout
        sys.stdout = sys.stderr
        try:
            result = handler(args)
            text = json.dumps(result, ensure_ascii=False, default=str)
            return _result(id_, {"content": [{"type": "text", "text": text}],
                                 "isError": False})
        except Exception as e:
            text = json.dumps({"ok": False, "error": str(e),
                               "traceback": traceback.format_exc()[-800:]},
                              ensure_ascii=False)
            return _result(id_, {"content": [{"type": "text", "text": text}],
                                 "isError": True})
        finally:
            sys.stdout = real_stdout
    if method.startswith("notifications/"):
        return None
    return _error(id_, -32601, f"未知方法 {method!r}")


def serve(stdin=None, stdout=None) -> None:
    """stdio 主循环：每行一条 JSON-RPC 消息，直到输入关闭。"""
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    try:
        stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass
    for raw in stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            resp = _error(None, -32700, f"Parse error: {e}")
        else:
            resp = handle_message(msg)
        if resp is not None:
            stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            stdout.flush()


if __name__ == "__main__":
    serve()
