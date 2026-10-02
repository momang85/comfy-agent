# comfy-agent MCP server

把 comfy-agent 的执行引擎暴露成 MCP 工具，让 **Claude Desktop / Cursor / Claude Code**
等任何 MCP 客户端把本机 ComfyUI 当工具用：客户端自带的 LLM 当大脑（理解需求、组参数、读结果），
comfy-agent 引擎负责「本地校验 → 自动修复 → 提交执行 → 下载」。

- 零第三方依赖（纯标准库），stdio 传输，每行一条 JSON-RPC 消息
- 绝不 import brain/——大脑由 MCP 客户端扮演；引擎侧路径安全规则与 CLI 相同
- 协议版本支持 2025-06-18 / 2025-03-26 / 2024-11-05（回退兼容）

## 工具一览

| 工具 | 作用 | 要点 |
|---|---|---|
| `comfy_status` | 服务器状态（版本/GPU/显存） | ComfyUI 没跑时返回结构化提示，不抛异常 |
| `comfy_inspect` | 本机能力摘要（节点/模型/适配） | 让客户端 LLM 一次了解这台机器能干什么 |
| `comfy_list_templates` | 模板目录 + 参数表 | 生成前先查 id 与参数 |
| `comfy_run_template` | 执行模板（完整校验-修复管线） | 返回 stage/ok/outputs/repairs/validation_issues；图像一般 15–90s |
| `comfy_run_workflow` | 跑任意工作流文件 | UI 格式自动转 API 格式；同一条管线 |
| `comfy_upload_image` | 上传图片到 /input | 返回 server_name 供 image 参数用；路径限允许目录 |
| `comfy_models` | 列模型（可按 folder 过滤） | |

## 客户端配置

`command` 里的 python 用任意 3.10+（ComfyUI 整合包自带的也行）；
`<repo>` 换成 comfy-agent 仓库的绝对路径。

**Claude Desktop**（`claude_desktop_config.json`）：

```json
{
  "mcpServers": {
    "comfy-agent": {
      "command": "python",
      "args": ["<repo>\\scripts\\mcp_server.py"]
    }
  }
}
```

**Cursor**（`~/.cursor/mcp.json`）：同上结构，放 `mcpServers` 下。

**Claude Code**（CLI 一条命令）：

```bash
claude mcp add comfy-agent -- python "<repo>/scripts/mcp_server.py"
```

启动器 `scripts/mcp_server.py` 自行推导仓库根目录，客户端无需设 cwd 或 PYTHONPATH。
也可以直接模块方式跑：`python -m comfy_agent.mcp_server`（cwd=仓库根）。

## 提问示例

配置好后对 Claude 说：

- 「看看我这台机器的 ComfyUI 能力摘要」→ comfy_inspect
- 「用模板画一只戴宇航头盔的橘猫，1024x1024，2 张」→ comfy_list_templates → comfy_run_template
- 「把桌面上的 cat.png 上传，然后用 i2i 改成吉卜力风格」→ comfy_upload_image → comfy_run_template

## 语义修复与边界

- 引擎的结构化失败（validation_failed / execution_failed / suggestion）原样返回给客户端 LLM，
  由它决定改参数重试或换路线——与 Web UI 里大脑看到的诊断完全一致
- 缺模型时会返回缺哪个文件、放哪个目录；不会静默换成别的模型
- VLM 评估只在 ⚙/settings.json 配置了视觉模型时进行；没配则返回 Tier0 确定性检查结果
- ComfyUI 必须已在 127.0.0.1:8188 运行（SSRF 防护默认仅回环）
