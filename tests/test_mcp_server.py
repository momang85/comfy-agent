# -*- coding: utf-8 -*-
"""MCP server 测试：协议层纯内存 + 一次真实子进程冒烟。

不依赖 ComfyUI 在跑：comfy_status 在服务不可达时也必须返回结构化结果
（ok=false），这本身就是工具契约的一部分。
"""
import io
import json
import subprocess
import sys
import unittest

from comfy_agent import mcp_server


def rpc(id_, method, params=None):
    return {"jsonrpc": "2.0", "id": id_, "method": method,
            "params": params or {}}


class ProtocolTests(unittest.TestCase):
    def test_initialize_echoes_supported_version(self):
        r = mcp_server.handle_message(rpc(1, "initialize",
                                         {"protocolVersion": "2025-03-26"}))
        self.assertEqual(r["result"]["protocolVersion"], "2025-03-26")
        self.assertIn("tools", r["result"]["capabilities"])
        self.assertEqual(r["result"]["serverInfo"]["name"], "comfy-agent-mcp")

    def test_initialize_unknown_version_falls_back(self):
        r = mcp_server.handle_message(rpc(1, "initialize",
                                          {"protocolVersion": "1999-01-01"}))
        self.assertIn(r["result"]["protocolVersion"],
                      mcp_server.SUPPORTED_VERSIONS)

    def test_tools_list_has_seven_tools_with_schemas(self):
        r = mcp_server.handle_message(rpc(2, "tools/list"))
        tools = r["result"]["tools"]
        names = {t["name"] for t in tools}
        self.assertEqual(len(tools), 7)
        self.assertTrue({"comfy_status", "comfy_inspect", "comfy_list_templates",
                         "comfy_run_template", "comfy_run_workflow",
                         "comfy_upload_image", "comfy_models"} <= names)
        for t in tools:
            self.assertEqual(t["inputSchema"]["type"], "object")
            self.assertTrue(t["description"])

    def test_unknown_tool_is_protocol_error(self):
        r = mcp_server.handle_message(rpc(3, "tools/call",
                                          {"name": "nope", "arguments": {}}))
        self.assertEqual(r["error"]["code"], -32602)

    def test_unknown_method(self):
        r = mcp_server.handle_message(rpc(4, "resources/list"))
        self.assertEqual(r["error"]["code"], -32601)

    def test_notification_yields_no_response(self):
        self.assertIsNone(mcp_server.handle_message(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_tools_call_list_templates_roundtrip(self):
        r = mcp_server.handle_message(rpc(5, "tools/call",
                                          {"name": "comfy_list_templates",
                                           "arguments": {}}))
        self.assertFalse(r["result"]["isError"])
        payload = json.loads(r["result"]["content"][0]["text"])
        self.assertTrue(payload["ok"])
        self.assertTrue(any(t["id"] == "t2i" for t in payload["templates"]))

    def test_tools_call_status_without_server_is_structured(self):
        # ComfyUI 不在跑也必须结构化降级（连接拒绝是秒回的）
        r = mcp_server.handle_message(rpc(6, "tools/call",
                                          {"name": "comfy_status",
                                           "arguments": {}}))
        payload = json.loads(r["result"]["content"][0]["text"])
        self.assertIn("ok", payload)
        self.assertIn("hint", payload)

    def test_run_template_empty_id_rejected_before_network(self):
        r = mcp_server.handle_message(rpc(7, "tools/call",
                                          {"name": "comfy_run_template",
                                           "arguments": {"template_id": ""}}))
        payload = json.loads(r["result"]["content"][0]["text"])
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["stage"], "render_failed")

    def test_run_workflow_path_traversal_blocked(self):
        r = mcp_server.handle_message(rpc(8, "tools/call",
                                          {"name": "comfy_run_workflow",
                                           "arguments": {"path": "../../etc/passwd"}}))
        payload = json.loads(r["result"]["content"][0]["text"])
        self.assertFalse(payload["ok"])
        self.assertIn("error", payload)

    def test_stdout_guard_keeps_protocol_clean(self):
        # 引擎侧 print 必须进 stderr，绝不进协议 stdout
        import sys as _sys
        err_cap = io.StringIO()
        real_err = _sys.stderr
        _sys.stderr = err_cap
        try:
            r = mcp_server.handle_message(rpc(9, "tools/call",
                                              {"name": "comfy_list_templates",
                                               "arguments": {}}))
        finally:
            _sys.stderr = real_err
        self.assertFalse(r["result"]["isError"])

    def test_serve_loop_roundtrip(self):
        lines = "\n".join([
            json.dumps(rpc(1, "initialize", {"protocolVersion": "2024-11-05"})),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            "not-json",                                    # 解析错误 → -32700
            json.dumps(rpc(2, "tools/list")),
            "",
        ])
        out = io.StringIO()
        mcp_server.serve(stdin=io.StringIO(lines), stdout=out)
        replies = [json.loads(x) for x in out.getvalue().splitlines() if x.strip()]
        self.assertEqual(len(replies), 3)          # 通知无响应
        self.assertEqual(replies[0]["id"], 1)
        self.assertEqual(replies[0]["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual(replies[1]["error"]["code"], -32700)
        self.assertEqual(len(replies[2]["result"]["tools"]), 7)

    def test_stdout_pollution_does_not_reach_protocol(self):
        # 注册一个会 print 的假工具，验证协议帧仍然干净
        def noisy(args):
            print("ENGINE NOISE")
            return {"ok": True}
        mcp_server.TOOL_HANDLERS["_noisy"] = noisy
        try:
            lines = "\n".join([
                json.dumps(rpc(1, "tools/call",
                               {"name": "_noisy", "arguments": {}})),
            ])
            out = io.StringIO()
            real_err, err_cap = sys.stderr, io.StringIO()
            sys.stderr = err_cap
            try:
                mcp_server.serve(stdin=io.StringIO(lines), stdout=out)
            finally:
                sys.stderr = real_err
            self.assertEqual(len(out.getvalue().splitlines()), 1)
            self.assertIn("ENGINE NOISE", err_cap.getvalue())
        finally:
            mcp_server.TOOL_HANDLERS.pop("_noisy", None)


class SubprocessSmokeTests(unittest.TestCase):
    """真实 stdio 子进程：python -m comfy_agent.mcp_server"""

    def test_subprocess_protocol_smoke(self):
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        proc = subprocess.Popen(
            [sys.executable, "-B", "-m", "comfy_agent.mcp_server"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=root, text=True, encoding="utf-8")
        try:
            msgs = [
                json.dumps(rpc(1, "initialize", {"protocolVersion": "2025-03-26"})),
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                json.dumps(rpc(2, "tools/list")),
                json.dumps(rpc(3, "tools/call",
                               {"name": "comfy_status", "arguments": {}})),
            ]
            out, err = proc.communicate("\n".join(msgs) + "\n", timeout=60)
        finally:
            if proc.poll() is None:
                proc.kill()
        replies = [json.loads(x) for x in out.splitlines() if x.strip()]
        self.assertEqual(len(replies), 3, f"stderr: {err[-400:]}")
        self.assertEqual(replies[0]["result"]["protocolVersion"], "2025-03-26")
        self.assertEqual(len(replies[1]["result"]["tools"]), 7)
        payload = json.loads(replies[2]["result"]["content"][0]["text"])
        self.assertIn("ok", payload)
        self.assertFalse(replies[2]["result"]["isError"])


if __name__ == "__main__":
    unittest.main()
