# -*- coding: utf-8 -*-
"""技能沉淀与召回（COMFYCLAW 路线：成功轨迹 -> 可复用技能）。

- remember(): 任务成功（含失败→修复→成功轨迹）后沉淀技能
- recall():   新任务到来时按关键词重叠度召回相关技能（Phase 2 不用向量库）

存储：.comfy-agent/sessions/skills.json（JSON Lines 追加写，跨进程持久）
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

from comfy_agent import config

SKILLS_PATH = config.SESSIONS_DIR / "skills.json"
_lock = threading.Lock()

# 中文/英文分词（CJK 用二元组切分——无词典环境的轻量检索标准做法，
# "宇航头盔" -> "宇航/航头/头盔"，换字仍能命中；英文按词切）
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_EN_RE = re.compile(r"[a-z0-9][a-z0-9\-_]{2,}", re.I)
_PATH_RE = re.compile(
    r"[A-Za-z]:[\\\\/][^\s，。；;、：]*"          # 盘符路径
    r"|[^\s\u4e00-\u9fff，。；;、：]*[\\\\/]"     # 相对路径前缀（不跨中文）
    r"[^\s，。；;、：]*\.(png|jpg|jpeg|webp|json)",
    re.I)
_STOPWORDS = {"the", "and", "with", "for", "一个", "一张", "帮我", "生成",
              "图片", "图像", "一下", "什么", "这个", "那个", "风格", "画风",
              "背景", "画面", "请"}


def _strip_paths(text: str) -> str:
    """剥离 Windows/本地路径，避免路径碎片污染关键词。"""
    return _PATH_RE.sub(" ", text or "")


def _tokens(text: str) -> set[str]:
    text = _strip_paths(text)
    tokens: set[str] = set()
    for m in _EN_RE.finditer(text):
        tokens.add(m.group(0).lower())
    for m in _CJK_RE.finditer(text):
        run = m.group(0)
        for i in range(len(run) - 1):
            bg = run[i:i + 2]
            if bg not in _STOPWORDS:
                tokens.add(bg)
    return {t for t in tokens if t not in _STOPWORDS}


class SkillStore:
    def __init__(self, path: Path = None):
        self.path = Path(path or SKILLS_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ---------- 写入 ----------
    def remember(self, task: str, *, template: str = "", params: dict = None,
                 prompt: str = "", result: str = "", trajectory: list = None,
                 keywords: list = None) -> dict:
        """沉淀一条技能。trajectory: [{action, note}] 记录失败→修复→成功过程。"""
        skill = {
            "task": task,
            "keywords": keywords or sorted(_tokens(task)),
            "template": template,
            "params": params or {},
            "prompt": prompt,
            "result": result,
            "trajectory": trajectory or [],
            "ts": time.time(),
        }
        with _lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(skill, ensure_ascii=False) + "\n")
        return skill

    # ---------- 读取 ----------
    def all_skills(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        with self.path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return out

    def recall(self, task: str, top_k: int = 3, min_score: float = 0.15) -> list[dict]:
        """关键词重叠度召回。返回 [(score, skill)] 降序。"""
        q_tokens = _tokens(task)
        if not q_tokens:
            return []
        scored = []
        for s in self.all_skills():
            s_tokens = set(s.get("keywords", []) or _tokens(s.get("task", "")))
            if not s_tokens:
                continue
            inter = q_tokens & s_tokens
            score = len(inter) / max(len(q_tokens), 1)
            if score >= min_score:
                scored.append((score, s))
        scored.sort(key=lambda x: -x[0])
        return [s for _, s in scored[:top_k]]

    def format_for_llm(self, task: str, top_k: int = 3) -> str:
        """把召回技能格式化为注入大脑的记忆文本；无命中返回空串。
        失败/待确认的技能以"踩坑警告"呈现（避免复用），成功技能作为参考做法。"""
        hits = self.recall(task, top_k)
        if not hits:
            return ""
        lines = ["## 历史经验（相似任务的记录）"]
        for s in hits:
            ok = s.get("result") == "成功"
            tag = "参考做法" if ok else "⚠踩坑警告（勿照搬）"
            lines.append(f"- 任务「{s['task'][:80]}」→ 模板 {s.get('template', '')}"
                         f" 结果:{s.get('result', '?')} 【{tag}】")
            if ok and s.get("prompt"):
                lines.append(f"  提示词参考: {s['prompt'][:300]}")
            for t in s.get("trajectory", []):
                if t.get("action") in ("fix",) and t.get("note"):
                    lines.append(f"  踩坑修复经验: {t['note'][:200]}")
                if t.get("action") == "eval" and t.get("note"):
                    lines.append(f"  评估: {t['note'][:150]}")
        return "\n".join(lines)
