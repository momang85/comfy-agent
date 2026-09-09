# -*- coding: utf-8 -*-
"""LLM 客户端（OpenAI 兼容协议，BYOK，纯标准库）。

- chat(): 文本对话（大脑主循环用）
- see_image(): 视觉理解（GLM-4.5V 等 OpenAI 兼容 VLM，base64 图片）

安全约束（SSRF 防护）：
- 外部主机强制 https（防明文泄露 API key）
- 回环地址放行（Ollama/LM Studio 本地推理是合法场景），但仅限 http
- 阻断链路本地/云元数据地址（169.254.169.254 等真实攻击目标）
- 不跟随重定向

环境变量见 comfy_agent.config：
  LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
  VLM_BASE_URL / VLM_API_KEY / VLM_MODEL
"""
from __future__ import annotations

import base64
import ipaddress
import json
import os
import socket
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from comfy_agent import config

# 已知云元数据/内网服务端点（SSRF 高危目标，一律阻断）
_BLOCKED_HOSTS = {
    "metadata.google.internal", "instance-data", "metadata",
}
_BLOCKED_PREFIXES = ("169.254.", "fd00:ec2::")


class LLMError(Exception):
    pass


def _assert_safe_url(url: str, allow_loopback_http: bool = True) -> None:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if host.lower() in _BLOCKED_HOSTS:
        raise LLMError(f"安全限制：禁止访问元数据服务 {host}")
    try:
        infos = socket.getaddrinfo(host, parsed.port or 443,
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise LLMError(f"无法解析 LLM 主机名: {host}（{e}）") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if any(str(ip).startswith(p) for p in _BLOCKED_PREFIXES):
            raise LLMError(f"安全限制：禁止访问链路本地地址 {ip}")
        if ip.is_loopback:
            if not allow_loopback_http:
                raise LLMError(f"安全限制：{ip} 回环地址不被允许")
            # 本地推理服务器（Ollama 等）仅允许 http 明文
            if parsed.scheme != "http":
                raise LLMError("回环地址仅允许 http（本地推理服务器）")
        else:
            # 外部主机必须 https（保护 API key 不明文传输）
            if parsed.scheme != "https":
                raise LLMError(
                    f"安全限制：外部 LLM 服务必须使用 https（当前 {parsed.scheme}）")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(newurl, code, "重定向已被阻止", headers, fp)


class LLMClient:
    def __init__(self, base_url: str = None, api_key: str = None,
                 model: str = None):
        from comfy_agent import config as _cfg
        self._resolve(base_url, api_key, model, _cfg)
        self._opener = urllib.request.build_opener(_NoRedirect())

    def _resolve(self, base_url=None, api_key=None, model=None, _cfg=None):
        """配置解析链：显式参数 > 环境变量 > settings.json > 注册表/默认值。"""
        from comfy_agent import config as _cfg_inner
        cfg = _cfg_inner
        s = cfg.load_user_settings()
        self.base = (base_url or os.environ.get("LLM_BASE_URL")
                     or s.get("llm_base_url") or cfg.LLM_BASE_URL).rstrip("/")
        self.model = (model or os.environ.get("LLM_MODEL")
                      or s.get("llm_model") or cfg.LLM_MODEL)
        self.key = (api_key or os.environ.get("LLM_API_KEY")
                    or s.get("llm_api_key") or "")
        if not self.key:
            self.key = cfg.load_llm_api_key()   # settings.json -> 注册表

    def reload_settings(self) -> dict:
        """设置面板保存后热生效：重读配置，返回脱敏摘要。"""
        self._resolve()
        return self.masked()

    def masked(self) -> dict:
        k = self.key or ""
        return {"base_url": self.base, "model": self.model,
                "api_key_masked": (k[:4] + "…" + k[-4:]) if len(k) > 8
                else ("已设置" if k else "")}

    @property
    def ready(self) -> bool:
        return bool(self.key)

    def chat(self, messages: list[dict], temperature: float = 0.6,
             max_tokens: int = 4096, thinking: bool = None) -> str:
        """thinking=None 用模型默认；thinking=False 显式关闭思考模式
        （结构化抽取任务用——思考会吃光 token 导致空回复）。"""
        if not self.ready:
            raise LLMError(
                "未配置 LLM_API_KEY。请设置环境变量（支持任意 OpenAI 兼容服务："
                "智谱/DeepSeek/Kimi/OpenAI 等）。")
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if thinking is False:
            body["thinking"] = {"type": "disabled"}
        return self._post(body)

    def chat_stream(self, messages: list[dict], temperature: float = 0.6,
                    max_tokens: int = 4096, thinking: bool = None):
        """流式对话。yield (channel, delta)：
        channel="reasoning"（思考过程，thinking 模型）或 "content"（正文）。
        thinking=False 关闭思考模式（行动决策用，避免工具调用意图
        分流进 reasoning 通道导致解析丢失）。
        服务端拒绝流式时自动回退：yield ("content", 完整回复) 一次。"""
        if not self.ready:
            raise LLMError("未配置 LLM_API_KEY")
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if thinking is False:
            body["thinking"] = {"type": "disabled"}
        url = self.base + "/chat/completions"
        _assert_safe_url(url)
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.key}"})
        try:
            with self._opener.open(req, timeout=300) as resp:
                for raw in resp:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    delta = (chunk.get("choices") or [{}])[0].get("delta", {})
                    if delta.get("reasoning_content"):
                        yield ("reasoning", delta["reasoning_content"])
                    if delta.get("content"):
                        yield ("content", delta["content"])
        except urllib.error.HTTPError as e:
            # 流式被拒（400/405 等）→ 回退非流式
            if e.code in (400, 404, 405, 422):
                text = self.chat(messages, temperature, max_tokens)
                yield ("content", text)
                return
            detail = e.read().decode("utf-8", "replace")[:400]
            raise LLMError(f"LLM HTTP {e.code}: {detail}") from e
        except (ConnectionResetError, ConnectionError, TimeoutError,
                urllib.error.URLError, OSError) as e:
            raise LLMError(f"LLM 连接中断: {e}") from e

    def _post(self, body: dict) -> str:
        url = self.base + "/chat/completions"
        _assert_safe_url(url)
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.key}"})
        try:
            with self._opener.open(req, timeout=120) as resp:
                data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:400]
            raise LLMError(f"LLM HTTP {e.code}: {detail}") from e
        except (KeyError, IndexError) as e:
            raise LLMError(f"LLM 响应格式异常: {e}") from e
        except urllib.error.URLError as e:
            raise LLMError(f"无法连接 LLM 服务（{self.base}）: {e.reason}") from e
        except (ConnectionResetError, ConnectionError, TimeoutError, OSError) as e:
            raise LLMError(f"LLM 连接中断: {e}") from e


class VLMClient(LLMClient):
    """视觉评估客户端（OpenAI 兼容多模态格式）。"""

    def __init__(self):
        from comfy_agent import config as _cfg
        s = _cfg.load_user_settings()
        self._resolve(
            base_url=os.environ.get("VLM_BASE_URL") or s.get("vlm_base_url")
            or _cfg.VLM_BASE_URL,
            api_key=os.environ.get("VLM_API_KEY") or s.get("vlm_api_key")
            or _cfg.VLM_API_KEY or s.get("llm_api_key"),
            model=os.environ.get("VLM_MODEL") or s.get("vlm_model")
            or _cfg.VLM_MODEL,
        )
        if not self.key:
            self.key = _cfg.load_llm_api_key()
        self._opener = urllib.request.build_opener(_NoRedirect())

    def see_image(self, image_path: str | Path, instruction: str) -> str:
        """看图并回答指令。"""
        p = Path(image_path)
        if not p.exists():
            raise LLMError(f"图片不存在: {p}")
        b64 = base64.b64encode(p.read_bytes()).decode()
        mime = "image/png" if p.suffix.lower() == ".png" else \
            "image/jpeg" if p.suffix.lower() in (".jpg", ".jpeg") else \
            "image/webp"
        body = {
            "model": self.model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {
                        "url": f"data:{mime};base64,{b64}"}},
                    {"type": "text", "text": instruction},
                ],
            }],
            "max_tokens": 1500,
            "temperature": 0.2,
            "thinking": {"type": "disabled"},   # 结构化输出，思考会吃光token
        }
        return self._post(body)

    def judge_json(self, image_path: str | Path, criteria: str) -> dict:
        """按标准评估图片，返回区域级诊断 JSON：
        {pass: bool, score: 0-10, issues: [{location, description, fix_hint}], advice}"""
        prompt = (
            "你是 AI 生成图像的质量审核员。请依据以下要求评估这张图：\n"
            f"{criteria}\n\n"
            "严格按以下 JSON 格式回答（不要输出其他内容）：\n"
            '{"pass": true/false, "score": 0-10 的整数, '
            '"issues": [{"location": "画面中具体部位（如：猫的头部/背景/右下角）", '
            '"description": "问题描述", '
            '"fix_hint": "给生成器的修复提示（指明应修改哪个提示词关键词或哪个生成参数）"}], '
            '"advice": "整体改进建议"}\n'
            "评分标准：9-10 完全符合要求；7-8 基本符合有小瑕疵；"
            "5-6 明显偏差；<5 严重不符。fix_hint 必须可执行（指出词或参数名）。")
        raw = self.see_image(image_path, prompt)
        return _extract_json(raw)

    def analyze_image_json(self, image_path: str | Path) -> dict:
        """分析用户上传的图片，返回结构化 JSON：
        {content, style, colors, composition, quality_issues,
         recommended: {template, positive_prompt, negative_prompt, params}}"""
        prompt = (
            "你是 AI 绘画工作流规划助手。请分析这张用户提供的图片，"
            "为后续生成任务提供依据。严格按以下 JSON 格式回答"
            "（不要输出其他内容，中文填写）：\n"
            '{"content": "画面内容（主体+动作+场景，一句话）", '
            '"style": "画风（动漫/写实/厚涂/线稿等）", '
            '"colors": "主色调", '
            '"composition": "构图（特写/全身/三分法/背景占比）", '
            '"quality_issues": ["图像自身问题（模糊/畸形/噪点等，无则空数组）"], '
            '"recommended": {'
            '"template": "从这些中选择最合适的：t2i / i2i / style_transfer", '
            '"positive_prompt": "若要把这张图转绘/复刻，建议的英文标签提示词（booru风格，逗号分隔）", '
            '"negative_prompt": "建议的负面提示词（英文标签）", '
            '"params": {"denoise": 0.0-1.0 建议值, "cfg": 建议值}'
            '}}')
        raw = self.see_image(image_path, prompt)
        return _extract_json(raw)


def _extract_json(text: str) -> dict:
    """从 LLM 回复中提取 JSON（容忍 ```json 包裹与前后废话）。"""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    s, e = text.find("{"), text.rfind("}")
    if s >= 0 and e > s:
        try:
            return json.loads(text[s:e + 1])
        except json.JSONDecodeError:
            pass
    return {"pass": None, "score": None, "issues": [],
            "advice": f"评估器返回无法解析: {text[:200]}"}
