# -*- coding: utf-8 -*-
"""节点知识层：object_info 快照索引 + ComfyUI-Manager 缓存融合。

解决的空白点：ComfyUI Registry 只有包级元数据、没有 pin 级 schema；
pin 级数据只存在于运行实例的 /object_info（6万节点全量塞不进 LLM 上下文）。
本模块把本机 1527 个节点的签名做成可检索索引，并融合 Manager 的
节点→安装包映射（缺失节点可直接定位到安装包）。
"""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

from . import config
from .client import Client, ComfyUIError

SNAPSHOT_PATH = config.KNOWLEDGE_DIR / "object_info_snapshot.json"
# Manager 缓存：节点类名 -> 所属自定义节点包
EXT_MAP_CANDIDATES = ("extension-node-map.json", "1514988643_custom-node-list.json")


class Knowledge:
    def __init__(self, snapshot: dict, ext_map: dict | None = None,
                 models: dict | None = None):
        self.snapshot = snapshot            # {node_class: object_info_entry}
        self.ext_map = ext_map or {}        # {node_class: [包名...]}
        self.models = models or {}          # {folder: [文件名]}

    # ---------- 构建 ----------
    @classmethod
    def build(cls, client: Client | None = None, refresh: bool = False) -> "Knowledge":
        client = client or Client()
        snapshot = None
        if not refresh and SNAPSHOT_PATH.exists():
            try:
                snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
            except Exception:
                snapshot = None
        if snapshot is None:
            snapshot = client.object_info()
            SNAPSHOT_PATH.write_text(
                json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
        ext_map = cls._load_manager_extmap()
        try:
            models = client.models()
        except ComfyUIError:
            models = {}
        return cls(snapshot, ext_map, models)

    @staticmethod
    def _load_manager_extmap() -> dict:
        """读 Manager 缓存（节点类名 -> 包仓库 URL/名称列表）。
        形态: {git_url: [[节点名...], {title_aux: ...}]} 或 {pkg: {node_list: [...]}}。"""
        cache_dir = config.MANAGER_CACHE
        ext_map = {}
        for path in sorted(cache_dir.glob("*extension-node-map.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            for repo, info in data.items():
                if isinstance(info, dict):
                    nodes = info.get("node_list", [])
                elif isinstance(info, list) and info and isinstance(info[0], list):
                    nodes = info[0]
                    # info[1] 可能带 title_aux（更友好的包名）
                    if len(info) > 1 and isinstance(info[1], dict) and info[1].get("title_aux"):
                        repo = info[1]["title_aux"]
                else:
                    continue
                for node in nodes or []:
                    ext_map.setdefault(node, []).append(repo)
        return ext_map

    # ---------- 查询 ----------
    def has_node(self, node_class: str) -> bool:
        return node_class in self.snapshot

    def node_info(self, node_class: str) -> dict | None:
        return self.snapshot.get(node_class)

    def node_meta(self, node_class: str) -> dict:
        """语义元数据：display_name/category/description/aliases（0.33 提供）。"""
        info = self.snapshot.get(node_class) or {}
        return {
            "display_name": info.get("display_name", ""),
            "category": info.get("category", ""),
            "description": info.get("description", ""),
            "aliases": info.get("search_aliases", []),
        }

    # 中文概念 -> 英文节点名关键词（语义搜索用）
    CONCEPT_ALIASES = {
        "采样": ["sampler", "ksampler"], "放大": ["upscale", "resize", "scale"],
        "编码": ["encode"], "解码": ["decode"], "加载": ["loader", "load"],
        "保存": ["save"], "图像": ["image"], "视频": ["video"],
        "提示词": ["clip", "text", "prompt"], "条件": ["conditioning"],
        "潜空间": ["latent"], "模型": ["model", "checkpoint"],
        "抠图": ["rmbg", "segment", "background"], "人脸": ["face", "detailer"],
        "姿势": ["pose", "openpose"], "边缘": ["canny", "lineart", "line"],
        "深度": ["depth"], "拼接": ["concat", "batch"], "遮罩": ["mask"],
        "LoRA": ["lora"], "修复": ["inpaint", "repair"],
    }

    def find_nodes(self, keyword: str, limit: int = 15,
                   category: str = None) -> list[dict]:
        """关键词搜索节点（含中文概念别名、类别过滤、display_name 匹配）。"""
        kw = keyword.lower()
        # 中文概念 → 英文候选词展开
        candidates = [kw]
        for zh, ens in self.CONCEPT_ALIASES.items():
            if zh in kw or zh in keyword:
                candidates.extend(ens)
        exact, prefix, contains, fuzzy = [], [], [], []
        for name in self.snapshot:
            meta = self.node_meta(name)
            if category and category not in (meta["category"] or ""):
                continue
            n_l = name.lower()
            d_l = (meta["display_name"] or "").lower()
            blob = f"{n_l} {d_l} {' '.join(meta['aliases'] or [])}"
            if any(c and (n_l == c or d_l == c) for c in candidates):
                exact.append(name)
            elif any(c and blob.startswith(c) for c in candidates):
                prefix.append(name)
            elif any(c and c in blob for c in candidates):
                contains.append(name)
            else:
                score = max(SequenceMatcher(None, c, n_l).ratio()
                            for c in candidates) if candidates else 0
                if score > 0.55:
                    fuzzy.append((score, name))
        result = exact + prefix + contains
        result += [n for _, n in sorted(fuzzy, reverse=True)]
        return [{"class": n, "package": self.ext_map.get(n, ["(核心节点)"]),
                 "category": self.node_meta(n)["category"],
                 "display_name": self.node_meta(n)["display_name"]}
                for n in result[:limit]]

    def categories(self) -> list[tuple[str, int]]:
        """类别分布（按节点数降序）。"""
        from collections import Counter
        c = Counter(self.node_meta(n)["category"] or "(无类别)"
                    for n in self.snapshot)
        return c.most_common()

    def input_order(self, node_class: str) -> list[str]:
        """widget 值顺序 = required + optional 的键序（转换器依赖）。
        优先用服务器提供的 input_order 权威顺序（0.3x），缺失时回退键序。"""
        info = self.snapshot.get(node_class) or {}
        inputs = info.get("input", {})
        declared = info.get("input_order") or {}
        order = list(declared.get("required", []))
        if not order:
            order = list(inputs.get("required", {}).keys())
        opt_declared = list(declared.get("optional", []))
        if opt_declared:
            order += opt_declared
        else:
            order += list(inputs.get("optional", {}).keys())
        return order

    def widget_input_names(self, node_class: str) -> list[str]:
        """只返回 widget 型输入（值来自 widgets_values，非连线）。"""
        info = self.snapshot.get(node_class) or {}
        inputs = info.get("input", {})
        names = []
        for section in ("required", "optional"):
            for name, spec in inputs.get(section, {}).items():
                if isinstance(spec, list) and spec and isinstance(spec[0], str):
                    # 类型为字符串（INT/FLOAT/STRING/COMBO）而非连线类型
                    if not spec[0].startswith("IMAGE,MASK") and spec[0] not in (
                            "MODEL", "CLIP", "VAE", "CONDITIONING", "LATENT",
                            "IMAGE", "MASK", "CONTROL_NET", "GUIDER", "NOISE",
                            "SAMPLER", "SIGMAS", "AUDIO"):
                        names.append(name)
        return names

    def enum_choices(self, node_class: str, input_name: str) -> list | None:
        """取某输入的枚举选项；非枚举返回 None。"""
        info = self.snapshot.get(node_class) or {}
        for section in ("required", "optional"):
            spec = info.get("input", {}).get(section, {}).get(input_name)
            if spec and isinstance(spec, list) and spec:
                if isinstance(spec[0], list):          # [[choices], {opts}]
                    return spec[0]
                if isinstance(spec[0], str) and len(spec) >= 2 and \
                        isinstance(spec[1], dict) and "choices" in spec[1]:
                    return spec[1]["choices"]           # ["COMBO", {choices}]
        return None

    def is_combo_of_files(self, node_class: str, input_name: str,
                          client: Client = None) -> Optional[list]:
        """文件选择型输入（如 ckpt_name）：返回合法文件名列表（优先本机模型清单）。"""
        choices = self.enum_choices(node_class, input_name)
        if choices and any(str(c).endswith(SAFE_EXT) for c in choices for SAFE_EXT in
                           (".safetensors", ".pt", ".ckpt", ".gguf", ".pth")):
            return choices
        return None

    def package_of(self, node_class: str) -> str:
        pkgs = self.ext_map.get(node_class)
        return pkgs[0] if pkgs else "(核心节点)"

    # ---------- 模型 ----------
    def find_model(self, query: str, folders: list[str] = None) -> list[dict]:
        """在模型清单中模糊查找。返回 [{name, folder, score}]。"""
        q = (query or "").lower().replace("\\", "/")
        out = []
        for folder, files in self.models.items():
            if folders and folder not in folders:
                continue
            for f in files:
                fname = str(f).lower().replace("\\", "/")
                score = 0.0
                if fname == q or fname.rsplit("/", 1)[-1] == q:
                    score = 1.0
                elif q in fname:
                    score = 0.8
                else:
                    score = SequenceMatcher(None, q, fname).ratio() * 0.6
                if score >= 0.35:
                    out.append({"name": f, "folder": folder, "score": round(score, 3)})
        out.sort(key=lambda x: -x["score"])
        return out[:10]

    def all_checkpoints(self) -> list[str]:
        return list(self.models.get("checkpoints", []))

    def resolve_model_name(self, name: str, folders: list[str] = None):
        """把请求的模型名解析为本机清单里的**精确条目**（分隔符不敏感匹配）。

        模板默认值用 POSIX 分隔符以保持跨平台，而 ComfyUI 的枚举在 Windows 上
        是反斜杠形态；提交前必须换成清单里的原始字符串，否则服务器报
        value_not_in_list（实测：同一模型 `/` 形态被拒、`\\` 形态成功）。
        解析不到返回 None。"""
        if not name:
            return None
        target = str(name).lower().replace("\\", "/")

        def _search(compare) -> Optional[str]:
            for folder, files in self.models.items():
                if folders and folder not in folders:
                    continue
                for f in files:
                    if compare(str(f).lower().replace("\\", "/")):
                        return str(f)
            return None

        hit = _search(lambda f: f == target)
        if hit:
            return hit
        base = target.rsplit("/", 1)[-1]
        return _search(lambda f: f.rsplit("/", 1)[-1] == base)

    def summary_for_llm(self) -> str:
        """给 LLM 的知识摘要（控制篇幅）。"""
        lines = [f"本机 ComfyUI 共 {len(self.snapshot)} 个节点类。"]
        cps = self.all_checkpoints()
        if cps:
            lines.append("checkpoints: " + ", ".join(cps))
        for folder in ("diffusion_models", "loras", "vae", "text_encoders", "upscale_models"):
            files = self.models.get(folder, [])
            if files:
                lines.append(f"{folder}: " + ", ".join(str(f) for f in files[:8]))
        return "\n".join(lines)


# 文件后缀常量（is_combo_of_files 使用）
SAFE_EXT = (".safetensors", ".pt", ".ckpt", ".gguf", ".pth")
