# -*- coding: utf-8 -*-
"""引擎层 CLI（JSON 输出，供脚本与大脑调用）。

用法（用整合包 Python 或任何 3.10+）：
  python -m comfy_agent.cli status
  python -m comfy_agent.cli templates
  python -m comfy_agent.cli models [folder]
  python -m comfy_agent.cli convert <ui_workflow.json> [--out out.json]
  python -m comfy_agent.cli run <template_id> --json '{"prompt":"a cat"}'
  python -m comfy_agent.cli upload <image_path>
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

# convert 允许读取的根目录（项目本身 + ComfyUI 安装目录 + 常见用户目录）
def _allowed_roots() -> list[Path]:
    from . import config
    roots = [Path(__file__).resolve().parent.parent,
             Path.home() / "Desktop",
             Path.home() / "Downloads"]
    if config.COMFY_ROOT.exists():
        roots.append(config.COMFY_ROOT)
    return roots


def _safe_read_path(raw: str) -> Path:
    """路径安全：规范化、禁止 ../ 穿越、限制在允许目录内。"""
    p = Path(raw).expanduser()
    # 拒绝显式穿越
    if ".." in p.parts:
        raise ValueError("路径不允许包含 ..")
    rp = p.resolve()
    if not rp.exists():
        raise ValueError(f"文件不存在: {rp}")
    allowed = _allowed_roots()
    if not any(str(rp).startswith(str(r.resolve()))
               for r in allowed if r.exists()):
        raise ValueError(f"路径超出允许范围（{[str(r) for r in allowed]}）: {rp}")
    return rp


def main():
    parser = argparse.ArgumentParser(prog="comfy_agent",
                                     description="ComfyUI 执行引擎 CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="服务器状态（版本/GPU/显存）")
    sub.add_parser("templates", help="模板目录")
    p_models = sub.add_parser("models", help="本机模型清单")
    p_models.add_argument("folder", nargs="?", default=None)
    p_conv = sub.add_parser("convert", help="UI工作流 -> API格式")
    p_conv.add_argument("path")
    p_conv.add_argument("--out", default=None)
    p_run = sub.add_parser("run", help="执行模板")
    p_run.add_argument("template_id")
    p_run.add_argument("--json", required=True, help="参数 JSON 字符串")
    p_run.add_argument("--no-wait", action="store_true")
    p_upload = sub.add_parser("upload", help="上传图片到 /input")
    p_upload.add_argument("path")
    p_refresh = sub.add_parser("refresh", help="刷新节点知识快照")
    sub.add_parser("inspect", help="本机知识摘要（给 LLM）")

    args = parser.parse_args()
    out = _dispatch(args)
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))


def _dispatch(args) -> dict:
    if args.cmd == "status":
        from .client import Client
        c = Client()
        return {"ok": True, "stats": c.system_stats()}
    if args.cmd == "templates":
        from .templates import catalog
        return {"ok": True, "templates": catalog()}
    if args.cmd == "models":
        from .client import Client
        c = Client()
        return {"ok": True, "models": c.models(args.folder)}
    if args.cmd == "convert":
        from .knowledge import Knowledge
        from .convert import convert_file
        try:
            src = _safe_read_path(args.path)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        try:
            api = convert_file(src, Knowledge.build())
            if args.out:
                outp = Path(args.out)
                if ".." in outp.parts:
                    return {"ok": False, "error": "输出路径不允许包含 .."}
                outp.parent.mkdir(parents=True, exist_ok=True)
                outp.write_text(
                    json.dumps(api, ensure_ascii=False, indent=1),
                    encoding="utf-8")
            return {"ok": True, "nodes": len(api), "saved": args.out,
                    "api": api if not args.out else None}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    if args.cmd == "run":
        from .runner import run_template
        try:
            params = json.loads(args.json)
        except json.JSONDecodeError as e:
            return {"ok": False, "error": f"参数 JSON 无效: {e}"}
        return run_template(args.template_id, params, wait=not args.no_wait)
    if args.cmd == "upload":
        from .runner import upload_input_image
        try:
            src = _safe_read_path(args.path)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        try:
            name = upload_input_image(src)
            return {"ok": True, "server_name": name}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    if args.cmd == "refresh":
        from .knowledge import Knowledge
        k = Knowledge.build(refresh=True)
        return {"ok": True, "nodes": len(k.snapshot),
                "ext_map": len(k.ext_map)}
    if args.cmd == "inspect":
        from .knowledge import Knowledge
        return {"ok": True, "summary": Knowledge.build().summary_for_llm()}
    return {"ok": False, "error": f"未知命令 {args.cmd}"}


if __name__ == "__main__":
    main()
