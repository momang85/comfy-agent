# -*- coding: utf-8 -*-
"""大脑入口：交互式 REPL 与一次性命令。

用法：
  python -m brain "画一只戴宇航帽的橘猫"          # 一次性任务
  python -m brain                                    # 交互模式
环境变量：LLM_BASE_URL / LLM_API_KEY / LLM_MODEL（必须）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brain.agent import Brain                     # noqa: E402
from brain.llm import LLMError                    # noqa: E402


def main():
    if "--web" in sys.argv or "-w" in sys.argv:
        from brain.web.server import serve
        serve()
        return
    if len(sys.argv) > 1:
        task = " ".join(sys.argv[1:])
        run_once(task)
    else:
        repl()


def run_once(task: str):
    brain = Brain(verbose=True)
    try:
        reply = brain.handle(task)
        print("\n" + "=" * 50)
        print(reply)
    except LLMError as e:
        print(f"LLM 错误: {e}", file=sys.stderr)
        sys.exit(1)


def repl():
    print("ComfyUI 大脑已启动（输入 exit 退出）")
    brain = Brain(verbose=True)
    while True:
        try:
            user = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user:
            continue
        if user.lower() in ("exit", "quit", "退出"):
            break
        try:
            reply = brain.handle(user)
            print(f"\n助手> {reply}")
        except LLMError as e:
            print(f"LLM 错误: {e}")
        except KeyboardInterrupt:
            print("\n（已中断当前任务）")


if __name__ == "__main__":
    main()
