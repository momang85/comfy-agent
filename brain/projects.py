# -*- coding: utf-8 -*-
"""项目隔离：不同工作项目拥有独立的 对话历史/技能记忆/产物/会话审计。

目录结构：
  .comfy-agent/projects/
    <project_id>/
      project.json      # {id, name, created}
      skills.jsonl      # 本项目技能记忆（LLM 上下文只注入本项目）
      history.jsonl     # 本项目会话审计
      outputs/          # 本项目产物（runner 输出到这里）

首次运行时把旧版全局 skills.jsonl 迁移为「默认项目」。
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

from comfy_agent import config

PROJECTS_ROOT = config.AGENT_HOME / "projects"
DEFAULT_NAME = "默认项目"
_ID_SAFE = re.compile(r"[^a-z0-9_-]")


def _safe_id(name: str) -> str:
    sid = _ID_SAFE.sub("", name.lower())[:20]
    # 中文/特殊字符名可能坍缩为空或单字母 → 用稳定短 hash 兜底
    if len(sid) < 2:
        import hashlib
        sid = "proj_" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
    return sid


class Project:
    def __init__(self, pid: str, name: str, created: float):
        self.id = pid
        self.name = name
        self.created = created

    @property
    def dir(self) -> Path:
        return PROJECTS_ROOT / self.id

    def outputs_dir(self) -> Path:
        d = self.dir / "outputs"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def uploads_dir(self) -> Path:
        d = self.dir / "uploads"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def skills_path(self) -> Path:
        return self.dir / "skills.jsonl"

    def history_path(self) -> Path:
        return self.dir / "history.jsonl"

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "created": self.created}


class ProjectStore:
    def __init__(self, root: Path = None):
        self.root = Path(root or PROJECTS_ROOT)
        self.root.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy()

    def _migrate_legacy(self) -> None:
        """旧版全局 skills.jsonl + outputs 目录 → 默认项目（只做一次）。"""
        if self.list():
            return
        default = self.create(DEFAULT_NAME)
        legacy_skills = config.SESSIONS_DIR / "skills.json"
        if legacy_skills.exists():
            try:
                content = legacy_skills.read_text(encoding="utf-8").strip()
                if content:
                    default.skills_path().write_text(content, encoding="utf-8")
            except OSError:
                pass
        # 旧产物目录整体迁移（同盘 rename；失败则保留原地不影响运行）
        legacy_outputs = config.RESULTS_DIR
        if legacy_outputs.exists() and not any(default.outputs_dir().iterdir()):
            import shutil
            try:
                for item in legacy_outputs.iterdir():
                    shutil.move(str(item), str(default.outputs_dir()))
            except OSError:
                pass

    def list(self) -> list[Project]:
        out = []
        for f in sorted(self.root.iterdir()):
            meta = f / "project.json"
            if meta.exists():
                try:
                    d = json.loads(meta.read_text(encoding="utf-8"))
                    out.append(Project(d["id"], d["name"], d.get("created", 0)))
                except (json.JSONDecodeError, KeyError, OSError):
                    continue
        return out

    def get(self, pid: str) -> Project | None:
        for p in self.list():
            if p.id == pid:
                return p
        return None

    def create(self, name: str) -> Project:
        sid = _safe_id(name)
        pid = sid
        i = 2
        while (self.root / pid).exists():
            pid = f"{sid}{i}"
            i += 1
        pdir = self.root / pid
        pdir.mkdir(parents=True, exist_ok=True)
        proj = Project(pid, name, time.time())
        (pdir / "project.json").write_text(
            json.dumps(proj.to_dict(), ensure_ascii=False, indent=1),
            encoding="utf-8")
        proj.outputs_dir()
        return proj

    def ensure_default(self) -> Project:
        existing = self.list()
        if existing:
            return existing[0]
        return self.create(DEFAULT_NAME)
