# -*- coding: utf-8 -*-
"""节点语义档案：LLM 按需生成、持久缓存（新节点也能当场学会）。

档案内容：中文用途、每个输入的解释与接线建议、输出含义、典型上下游、坑。
生成依据 = object_info 签名（类型/枚举/默认值）+ 可用描述——签名是
普适的，任何新装的节点都能现场生成正确档案。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from comfy_agent import config

PROFILES_PATH = config.KNOWLEDGE_DIR / "node_profiles.jsonl"
_lock = threading.Lock()


class ProfileStore:
    def __init__(self, path: Path = None):
        self.path = Path(path or PROFILES_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def get(self, node_class: str) -> dict | None:
        """读缓存档案。"""
        if not self.path.exists():
            return None
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("class") == node_class:
                return entry
        return None

    def put(self, entry: dict):
        with _lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def build_profile(node_class: str, knowledge, llm) -> dict:
    """用 LLM 从节点签名生成语义档案。"""
    info = knowledge.node_info(node_class)
    if info is None:
        raise ValueError(f"本机不存在节点 {node_class}")
    meta = knowledge.node_meta(node_class)
    # 紧凑签名：输入名/类型/默认值/枚举
    sig_lines = []
    for section in ("required", "optional"):
        for name, spec in info.get("input", {}).get(section, {}).items():
            t = spec[0] if isinstance(spec, list) and spec else spec
            opts = spec[1] if isinstance(spec, list) and len(spec) > 1 and \
                isinstance(spec[1], dict) else {}
            extra = ""
            if isinstance(t, list):
                extra = f"（枚举: {', '.join(map(str, t[:8]))}）"
            elif "default" in opts:
                extra = f"（默认 {opts['default']}）"
            sig_lines.append(f"  [{section}] {name}: {t}{extra}")
    outputs = info.get("output") or []
    out_names = info.get("output_name") or outputs

    prompt = f"""你是 ComfyUI 节点专家。请根据以下节点签名，为这个节点写一份
中文语义档案，供 AI 组装工作流时使用。

节点类名: {node_class}
显示名: {meta.get('display_name', '')}
类别: {meta.get('category', '')}
官方描述: {meta.get('description', '') or '（无）'}
输入签名:
{chr(10).join(sig_lines) if sig_lines else '（无输入）'}
输出: {list(zip(out_names, outputs))}

严格按以下 JSON 格式回答（不要输出其他内容）：
{{"purpose": "这个节点做什么（一句话中文）",
 "inputs": "每个输入的中文解释和接线建议（从上游什么节点接，或填什么值），逐条列出",
 "outputs": "每个输出接到下游什么类型的节点",
 "wiring": "典型接线模式（上游->本节点->下游）",
 "pitfalls": "常见坑（数组，可为空）"}}"""
    raw = llm.chat([{"role": "user", "content": prompt}],
                   temperature=0.2, max_tokens=1600, thinking=False)
    return _extract(prompt, raw, node_class)


def _extract(prompt: str, raw: str, node_class: str) -> dict:
    """解析 LLM 回复为档案 dict（容忍围栏）。"""
    from .llm import _extract_json
    d = _extract_json(raw)
    d.setdefault("class", node_class)
    d.setdefault("purpose", d.get("purpose", raw[:200]))
    return d


def learn(node_class: str, knowledge, llm, store: ProfileStore = None) -> dict:
    """取档案：缓存命中直接用，未命中 LLM 生成并缓存。"""
    store = store or ProfileStore()
    cached = store.get(node_class)
    if cached:
        return cached
    entry = build_profile(node_class, knowledge, llm)
    store.put(entry)
    return entry


def format_profile(entry: dict) -> str:
    """档案 -> 注入对话的紧凑文本。"""
    lines = [f"【{entry.get('class')}】{entry.get('purpose', '')}"]
    for key, label in (("inputs", "输入"), ("outputs", "输出"),
                       ("wiring", "接线"), ("pitfalls", "坑")):
        val = entry.get(key)
        if val:
            if isinstance(val, list):
                lines.append(f"{label}: {'; '.join(map(str, val))[:400]}")
            else:
                lines.append(f"{label}: {val[:500]}")
    return "\n".join(lines)
