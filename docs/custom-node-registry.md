# 上架 Comfy Registry / ComfyUI-Manager 步骤

本仓库根目录已带 custom node 入口（`__init__.py` + `pyproject.toml` + 空的 `requirements.txt`），
Manager 克隆整仓即可作为一个名为 `comfy-agent` 的节点包使用。

## 节点是什么

`ComfyAgent Bridge (chat UI)`——画布上放一个、跑一次：确保 comfy-agent 的 Web UI
（`http://127.0.0.1:8899`）在跑（没跑就用 ComfyUI 自带的 python 以 detached 子进程拉起），
输出访问地址。Agent 以独立进程运行，崩溃不拖垮 ComfyUI。

## 用户手动安装（Registry 收录前）

```bash
cd <你的ComfyUI>/custom_nodes
git clone https://github.com/momang85/comfy-agent
# 重启 ComfyUI → 画布加 "ComfyAgent Bridge" 节点执行 → 浏览器打开 127.0.0.1:8899
```

或 ComfyUI-Manager → Install via Git URL → 粘贴仓库地址。

## 发布到 Comfy Registry（收录后可在 Manager 里搜索安装）

1. 在 [registry.comfy.org](https://registry.comfy.org) 用 GitHub 账号登录，创建 publisher（id 例如 `momang85`），生成一个 API token；
2. `pyproject.toml` 里 `[tool.comfy].PublisherId` 与之保持一致（当前已填 `momang85`）；
3. 安装 CLI：`pip install comfy-cli`；
4. 发布：`comfy registry publish`（会要求 token；版本号取自 `pyproject.toml` 的 `project.version`，每次发布前递增）；
5. 收录后 Manager 的 "Install Custom Nodes" 里即可搜到 "ComfyAgent"。

## 注意

- `requirements.txt` 刻意为空：本项目纯标准库，不要加任何依赖；
- 发版前确认 `__init__.py` 在 ComfyUI 进程里保持"零 import 脑/引擎"（只允许标准库 + 子进程拉起）；
- `node_search` 里的展示名以 `pyproject.toml` 的 `DisplayName` 为准。
