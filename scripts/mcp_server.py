#!/usr/bin/env python
"""comfy-agent MCP server 启动器：给不能设置 cwd 的 MCP 客户端用。

客户端配置里 command=python、args=[本文件绝对路径] 即可，
仓库位置从本文件推导，无需 PYTHONPATH。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from comfy_agent.mcp_server import serve  # noqa: E402

if __name__ == "__main__":
    serve()
