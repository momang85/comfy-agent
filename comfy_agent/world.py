# -*- coding: utf-8 -*-
"""世界模型：本机"有什么节点、有什么模型、模型由谁加载"的唯一真值入口。

存在的理由（项目 5 实证的浪费根源）：
1. 节点签名与模型枚举来自磁盘快照、无 TTL；模型清单来自 build 那一刻的
   `/models`，整个 Web 会话共用一份 → 下载了模型/装了节点，系统仍判"缺"。
2. 判断"有没有"的代码只查缓存、从不看盘 → 文件在盘上也不认。
3. 服务器拒绝枚举时拿陈旧快照的 choices[0] 顶上 → 会静默换模型。
4. 只回答"文件能不能下"，不回答"本机有没有能加载它的节点" → 项目 5 白下
   21.46MB（ultralytics 检测器没有对应的 UltralyticsDetectorProvider）。

规则：凡"能不能做 / 有没有"的判断都必须经过 WorldModel；它的负责范围包括
新鲜度（`refresh`）、落盘兜底（`model_available`）、加载链路（`loader_for`）。
"""
from __future__ import annotations

import time
from pathlib import Path

from . import config
from .knowledge import Knowledge

# 模型目录 → 能加载它的节点（按优先级）。用于回答"下这个模型有用吗"。
LOADER_HINTS: dict[str, list[str]] = {
    "checkpoints": ["CheckpointLoaderSimple", "CheckpointLoader"],
    "loras": ["LoraLoader", "LoraLoaderModelOnly"],
    "vae": ["VAELoader"],
    "vae_approx": ["TAESDLoader", "TAESDDecoder", "TAESDPreviewer"],
    "text_encoders": ["CLIPLoader", "DualCLIPLoader", "TripleCLIPLoader",
                      "LTXAVTextEncoderLoader"],
    "clip": ["CLIPLoader", "DualCLIPLoader", "CLIPVisionLoader"],
    "clip_vision": ["CLIPVisionLoader"],
    "controlnet": ["ControlNetLoader", "DiffControlNetLoader",
                   "ControlNetLoaderAdvanced"],
    "diffusion_models": ["UNETLoader"],
    "unet": ["UNETLoader"],
    "upscale_models": ["UpscaleModelLoader"],
    "style_models": ["StyleModelLoader"],
    "gligen": ["GLIGENLoader"],
    "photomaker": ["PhotoMakerLoader"],
    "ipadapter": ["IPAdapterModelLoader", "IPAdapterUnifiedLoader"],
    "audio_encoders": ["LTXVAudioVAELoader", "AudioEncoderLoader"],
    "model_patches": ["ModelPatchLoader"],
    "sams": ["SAMLoader", "SAMModelLoader (segment anything)",
             "SAMModelLoader"],
    "ultralytics": ["UltralyticsDetectorProvider", "AILab_YoloV8Adv",
                    "YoloV8DetectorProvider"],
    "onnx": ["ONNXDetectorProvider"],
    "diffusers": ["DiffusersLoader"],
    "llm_gguf": ["CLIPLoaderGGUF", "UnetLoaderGGUF"],
}

# 不靠节点加载的目录（由提示词/引擎直接使用）→ 不参与"能不能加载"判断
NON_NODE_FOLDERS = frozenset(("embeddings",))


def file_on_disk(name: str, folders: list[str] | None = None):
    """按分隔符归一在 models/ 里做单文件落盘校验；命中返回 Path。

    缓存（快照/清单）可能过期，这个检查只认文件系统。O(1) 直查（给了目录）
    或一层 glob（没给目录），不全盘扫描。
    """
    val = str(name or "").strip().replace("\\", "/").strip("/")
    if not val:
        return None
    root = Path(config.MODELS_DIR)
    tries = [root / f / val for f in (folders or [])]
    tries.append(root / val)
    for p in tries:
        try:
            if p.is_file():
                return p
        except OSError:
            continue
    try:
        base = val.rsplit("/", 1)[-1]
        # 清单里的子目录形态是 models/<folder>/<子目录>/<文件>（如 checkpoints/sdXL/x）
        for pattern in (f"*/{base}", f"*/*/{base}"):
            for p in root.glob(pattern):
                if p.is_file():
                    return p
    except OSError:
        pass
    return None


class WorldModel:
    """带新鲜度的本机世界视图（Knowledge 的实现细节）。"""

    def __init__(self, knowledge: Knowledge | None = None,
                 client=None, auto_fresh: bool = True):
        self._knowledge = knowledge
        self._client = client
        self.auto_fresh = auto_fresh
        self.refresh_log: list[dict] = []

    # ---------- 基础访问 ----------
    @property
    def knowledge(self) -> Knowledge:
        if self._knowledge is None:
            self._knowledge = Knowledge.build(client=self._client)
        return self._knowledge

    @property
    def revision(self) -> int:
        return getattr(self.knowledge, "revision", 0)

    @property
    def stale(self) -> bool:
        k = self.knowledge
        age = time.time() - getattr(k, "loaded_at", 0)
        return age > config.WORLD_SNAPSHOT_TTL

    def refresh(self, reason: str = "", force: bool = True) -> bool:
        """强制/按需重取节点签名与模型清单；返回是否发生变化。"""
        changed = self.knowledge.ensure_fresh(force=force)
        self.refresh_log.append({"ts": time.time(), "reason": reason,
                                 "changed": bool(changed),
                                 "revision": self.revision})
        return changed

    def maybe_fresh(self) -> None:
        """轻量调用前的自动保鲜（内部有 5 秒节流）。"""
        if self.auto_fresh:
            self.knowledge.ensure_fresh(force=False)

    # ---------- 节点 ----------
    def has_node(self, node_class: str) -> bool:
        self.maybe_fresh()
        return bool(node_class) and self.knowledge.has_node(node_class)

    def node_info(self, node_class: str) -> dict | None:
        self.maybe_fresh()
        return self.knowledge.node_info(node_class)

    def find_nodes(self, keyword: str, limit: int = 15, category: str = None):
        self.maybe_fresh()
        return self.knowledge.find_nodes(keyword, limit=limit, category=category)

    # ---------- 模型 ----------
    def model_available(self, name: str, folders: list[str] = None) -> bool:
        """缓存或**落盘**任一命中即算存在（防"文件在盘上还判缺"）。"""
        self.maybe_fresh()
        return self.knowledge.model_available(name, folders=folders)

    def find_model(self, query: str, folders: list[str] = None):
        self.maybe_fresh()
        return self.knowledge.find_model(query, folders=folders)

    def loader_for(self, folder: str) -> dict:
        """该模型目录由哪些节点加载，本机有没有。

        返回 {folder, candidates, present, best, usable, note}：
        - present：本机存在且可用的加载节点
        - usable：能不能用它加载（embeddings 这类不走节点 → True）
        """
        folder = (folder or "").strip()
        candidates = LOADER_HINTS.get(folder, [])
        present = [c for c in candidates if self.has_node(c)]
        if folder in NON_NODE_FOLDERS:
            return {"folder": folder, "candidates": candidates, "present": [],
                    "best": None, "usable": True,
                    "note": "该目录由提示词直接引用，不需要加载节点"}
        if not candidates:
            return {"folder": folder, "candidates": [], "present": [],
                    "best": None, "usable": True,
                    "note": f"未知/自定义目录 {folder!r}：无法判断加载节点，"
                            "请用 inspect_node 核对"}
        return {"folder": folder, "candidates": candidates, "present": present,
                "best": present[0] if present else None,
                "usable": bool(present),
                "note": "" if present else
                f"本机没有能加载 {folder} 的节点（试过：{'/'.join(candidates)}）"}

    def route_available(self, node_classes: list[str]) -> dict:
        """某条**具体链路**需要的节点是否齐备（比"有没有某个加载器"更精确）。

        项目 5 的教训：模型本身能被 AILab_YoloV8Adv 加载（所以"这模型有用"），
        但大脑想要的"检测器→SEGS→遮罩"链路需要 UltralyticsDetectorProvider，
        它没注册 → 那条链路不可用。区分这两件事才不会给错建议。
        包已装但节点不在时，最可能的原因是该节点的依赖没装成功，要如实这么说。
        """
        missing, present = [], []
        for cls in node_classes:
            (present if self.has_node(cls) else missing).append(cls)
        hints = []
        for cls in missing:
            pkg = self.package_of(cls)
            if pkg and pkg != "(核心节点)":
                hints.append(f"{cls}：{pkg} 已安装但该节点未注册"
                             "（多为依赖未装成功，请查 ComfyUI 启动日志）")
            else:
                hints.append(f"{cls}：本机没有这个节点")
        return {"available": not missing, "present": present, "missing": missing,
                "hints": hints}

    def model_usability(self, filename: str, folder: str) -> dict:
        """"下这个模型有用吗"的单一答案：文件是否已在 + 谁能加载它。"""
        info = self.loader_for(folder)
        on_disk = bool(self.knowledge.file_on_disk(filename, [folder])) \
            if filename else False
        return {"folder": folder, "loader": info["best"],
                "loader_candidates": info["candidates"],
                "loader_present": bool(info["present"]),
                "usable": bool(info["usable"]),
                "on_disk": on_disk, "note": info["note"]}

    # ---------- 包归属（安装建议必须核实） ----------
    def package_of(self, node_class: str) -> str:
        self.maybe_fresh()
        return self.knowledge.package_of(node_class)

    def paths(self) -> dict:
        root = Path(config.MODELS_DIR)
        return {"models_dir": str(root), "exists": root.is_dir(),
                "revision": self.revision, "stale": self.stale}
