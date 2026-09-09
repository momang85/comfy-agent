# -*- coding: utf-8 -*-
"""Skill 信息库全量实景化构建脚本。

流程：
  1. 语料预处理（零API）：90个 blueprints + 用户工作流 → API格式 → 
     反向索引 节点→使用它的工作流（含局部接线片段）＋ 使用频率
  2. 批量 LLM 生成全量节点档案（15个/批、thinking关闭、断点续跑）：
     输入材料 = 签名 + 本机模型枚举 + 真实用法片段 + 所属包（接地气）
  3. L0 索引 node_index.json：类名→一行用途+类别+使用频率（search_nodes 用）
  4. 家族技能 families/*.md（~15个功能家族）

用法：
  python scripts/build_node_skills.py --corpus          # 仅语料预处理
  python scripts/build_node_skills.py --generate        # 批量生成档案（断点续跑）
  python scripts/build_node_skills.py --index           # 重建 L0 索引
  python scripts/build_node_skills.py --families        # 生成家族技能
  python scripts/build_node_skills.py --all             # 全流程
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from comfy_agent import config                       # noqa: E402
from comfy_agent.convert import convert_file, ConversionError  # noqa: E402
from comfy_agent.knowledge import Knowledge          # noqa: E402
from brain.llm import LLMClient, LLMError, _extract_json  # noqa: E402
from brain.node_profiles import ProfileStore, _extract  # noqa: E402

BLUEPRINTS = config.COMFY_ROOT / "blueprints"
USAGE_PATH = config.KNOWLEDGE_DIR / "node_usage.json"
INDEX_PATH = config.KNOWLEDGE_DIR / "node_index.json"
FAMILIES_DIR = ROOT / "brain" / "skills" / "families"

BATCH = 15
REQUEST_PAUSE = 1.0


# ---------- 1. 语料预处理 ----------

def collect_corpus(knowledge: Knowledge) -> dict:
    """转换全部本地工作流语料，建反向索引 {node_class: {wfs: [..], freq: n}}。"""
    sources = list(BLUEPRINTS.glob("*.json")) + \
        list(config.USER_WORKFLOWS_DIR.glob("*.json"))
    usage: dict[str, dict] = {}
    converted, failed = 0, 0
    for path in sources:
        try:
            skipped = []
            api = convert_file(path, knowledge, strict=False, skipped=skipped)
        except (ConversionError, json.JSONDecodeError, OSError):
            failed += 1
            continue
        converted += 1
        wf_name = path.stem
        # 局部接线片段：每个节点的邻居（输入名->上游类名）
        for nid, node in api.items():
            cls = node.get("class_type", "")
            neighbors = {}
            for name, val in node.get("inputs", {}).items():
                if isinstance(val, list) and len(val) == 2 and \
                        isinstance(val[0], str) and val[0].isdigit():
                    src = api.get(val[0], {})
                    neighbors[name] = src.get("class_type", val[0])
            entry = usage.setdefault(cls, {"wfs": {}, "freq": 0})
            entry["freq"] += 1
            entry["wfs"].setdefault(wf_name, neighbors)
    USAGE_PATH.write_text(json.dumps(usage, ensure_ascii=False, indent=1),
                          encoding="utf-8")
    print(f"语料: 转换成功 {converted}/{len(sources)}，失败 {failed}；"
          f"覆盖节点 {len(usage)} 个")
    return usage


def usage_brief(usage: dict, cls: str, max_wfs: int = 2) -> str:
    entry = (usage or {}).get(cls)
    if not entry:
        return "（本机工作流语料中未出现）"
    lines = []
    for wf, neighbors in list(entry["wfs"].items())[:max_wfs]:
        nb = ", ".join(f"{k}←{v}" for k, v in list(neighbors.items())[:6])
        lines.append(f"《{wf}》中：{nb or '(无连线)'}")
    return "；".join(lines) + f"（本地使用 {entry['freq']} 次）"


# ---------- 2. 批量生成 ----------

def compact_signature(info: dict, max_inputs: int = 14) -> str:
    lines = []
    for section in ("required", "optional"):
        for name, spec in info.get("input", {}).get(section, {}).items():
            t = spec[0] if isinstance(spec, list) and spec else spec
            opts = spec[1] if isinstance(spec, list) and len(spec) > 1 and \
                isinstance(spec[1], dict) else {}
            extra = ""
            if isinstance(t, list):
                extra = f" 枚举[{', '.join(map(str, t[:6]))}{'…' if len(t) > 6 else ''}]"
            elif "default" in opts:
                extra = f" 默认{opts['default']}"
            lines.append(f"{name}:{t}{extra}")
            if len(lines) >= max_inputs:
                lines.append("…(更多输入省略)")
                return " ".join(lines)
    return " ".join(lines)


def generate_profiles(knowledge: Knowledge, usage: dict,
                      store: ProfileStore, limit: int = None,
                      batch: int = BATCH) -> int:
    llm = LLMClient()
    if not llm.ready:
        raise SystemExit("LLM_API_KEY 未配置")
    todo = [cls for cls in knowledge.snapshot
            if store.get(cls) is None]
    if limit:
        todo = todo[:limit]
    print(f"待生成 {len(todo)} 个节点档案（每批 {batch} 个）")
    done = 0
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        prompt = _batch_prompt(knowledge, usage, chunk)
        for attempt in range(3):
            try:
                raw = llm.chat([{"role": "user", "content": prompt}],
                               temperature=0.2, max_tokens=6000,
                               thinking=False)
                data = _extract_json(raw)
                break
            except LLMError as e:
                if attempt == 2:
                    print(f"  批 {i // batch} 失败: {e}")
                    data = {}
                time.sleep(2 * (attempt + 1))
        if not isinstance(data, dict):
            data = {}
        for cls in chunk:
            prof = data.get(cls)
            if isinstance(prof, dict):
                prof.setdefault("class", cls)
                prof.setdefault("purpose", "")
                prof.setdefault("one_line",
                                prof.get("purpose", "")[:40])
                store.put(prof)
                done += 1
        print(f"  批 {i // batch + 1}: {done}/{len(todo)} 完成")
        time.sleep(REQUEST_PAUSE)
    print(f"档案生成完成：新增 {done}")
    return done


def _batch_prompt(knowledge: Knowledge, usage: dict, chunk: list[str]) -> str:
    blocks = []
    for cls in chunk:
        info = knowledge.node_info(cls)
        meta = knowledge.node_meta(cls)
        blocks.append(
            f"### {cls}（{meta.get('display_name', '')} | {meta.get('category', '')} | "
            f"包:{knowledge.package_of(cls)}）\n"
            f"签名: {compact_signature(info)}\n"
            f"本机用法: {usage_brief(usage, cls)}")
    return ("你是 ComfyUI 节点专家。为以下每个节点写中文语义档案。"
            "输入材料含：签名（类型/枚举/默认值）+ 该节点在本机工作流中的"
            "真实用法。请基于这些真实情况写，不要编造不存在的参数。\n\n"
            + "\n\n".join(blocks) +
            "\n\n严格按以下 JSON 格式输出（以节点类名为键）：\n"
            '{"节点类名": {"purpose": "一句话中文用途", '
            '"inputs": "每个输入的解释与接线建议（从什么节点接/填什么值），分号分隔", '
            '"outputs": "输出接到什么类型的下游", '
            '"wiring": "典型接线模式", '
            '"pitfalls": ["坑1"]}, ...}')


# ---------- 3. L0 索引 ----------

def build_index(knowledge: Knowledge, usage: dict, store: ProfileStore) -> int:
    entries = {}
    for cls in knowledge.snapshot:
        meta = knowledge.node_meta(cls)
        prof = store.get(cls) or {}
        entries[cls] = {
            "display_name": meta.get("display_name", ""),
            "category": meta.get("category", ""),
            "one_line": prof.get("one_line") or prof.get("purpose", "")[:40],
            "package": knowledge.package_of(cls),
            "freq": (usage or {}).get(cls, {}).get("freq", 0),
        }
    INDEX_PATH.write_text(json.dumps(entries, ensure_ascii=False, indent=0),
                          encoding="utf-8")
    with_desc = sum(1 for e in entries.values() if e["one_line"])
    print(f"L0 索引: {len(entries)} 节点，{with_desc} 个有一行用途")
    return len(entries)


# ---------- 4. 家族技能 ----------

FAMILY_MAP = [
    ("model_loading", "模型加载", ["loader", "checkpoint", "unet", "model"],
     "checkpoint/UNET/CLIP/VAE/LoRA/ControlNet 等加载器"),
    ("conditioning", "文本与条件", ["condition", "clip", "text"],
     "提示词编码、条件链、CFGGuider"),
    ("sampling", "采样", ["sampler", "sigmas", "k"],
     "KSampler 系、调度器、采样参数"),
    ("latent", "潜空间", ["latent"],
     "空潜空间、VAE编码解码、潜空间放大"),
    ("image_io", "图像IO", ["image", "load", "save", "preview"],
     "加载/保存/预览图片"),
    ("image_process", "图像处理", ["image"],
     "缩放、裁切、颜色、滤镜、批处理"),
    ("upscale", "放大", ["upscale"],
     "放大模型与放大管线"),
    ("controlnet", "ControlNet", ["control"],
     "预处理、ControlNet加载与应用"),
    ("video", "视频", ["video"],
     "视频生成、帧处理、音视频合成"),
    ("audio", "音频", ["audio"],
     "音频生成与处理"),
    ("mask", "遮罩与分割", ["mask", "segment"],
     "遮罩处理、语义分割"),
    ("detection", "检测与姿态", ["detect", "pose", "face"],
     "人脸/姿态/目标检测"),
    ("logic", "逻辑与工具", ["utils", "logic", "string", "math"],
     "字符串/数学/开关/批处理工具"),
    ("inpaint", "修复与重绘", ["inpaint", "paint"],
     "局部重绘、外扩、修复"),
    ("other", "其他", [],
     "其余节点（分类兜底）"),
]


def generate_families(knowledge: Knowledge) -> int:
    llm = LLMClient()
    FAMILIES_DIR.mkdir(parents=True, exist_ok=True)
    # 按类别名归类到家族
    by_family = {fid: [] for fid, *_ in FAMILY_MAP}
    for cls in knowledge.snapshot:
        cat = (knowledge.node_meta(cls)["category"] or "").lower()
        matched = "other"
        for fid, _name, keywords, _desc in FAMILY_MAP:
            if any(k in cat for k in keywords):
                matched = fid
                break
        by_family[matched].append(cls)
    count = 0
    for fid, name, _kw, desc in FAMILY_MAP:
        classes = by_family[fid]
        if not classes:
            continue
        sample = classes[:20]
        prompt = (f"为 ComfyUI 的【{name}】节点家族写一份中文速查技能文档"
                  f"（markdown），供 AI 组装工作流时按需阅读。\n"
                  f"家族定位: {desc}\n"
                  f"本机该家族节点示例: {', '.join(sample)}\n"
                  f"要求：1) 家族通用接线模式 2) 关键节点用途与参数要点"
                  f" 3) 常见坑 4) 与相邻家族（上下游类型）的接口约定。"
                  f"控制在 600 字内。")
        try:
            doc = llm.chat([{"role": "user", "content": prompt}],
                           temperature=0.3, max_tokens=1400, thinking=False)
        except LLMError as e:
            print(f"  家族 {name} 生成失败: {e}")
            continue
        (FAMILIES_DIR / f"{fid}.md").write_text(
            f"# {name}节点家族速查\n\n{doc}", encoding="utf-8")
        count += 1
        time.sleep(REQUEST_PAUSE)
    print(f"家族技能: {count} 个")
    return count


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", action="store_true")
    ap.add_argument("--generate", action="store_true")
    ap.add_argument("--index", action="store_true")
    ap.add_argument("--families", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--batch", type=int, default=BATCH)
    args = ap.parse_args()
    do_all = args.all or not any(
        [args.corpus, args.generate, args.index, args.families])

    knowledge = Knowledge.build()
    store = ProfileStore()
    usage = json.loads(USAGE_PATH.read_text(encoding="utf-8")) \
        if USAGE_PATH.exists() else {}

    if args.corpus or do_all:
        usage = collect_corpus(knowledge)
    if args.generate or do_all:
        generate_profiles(knowledge, usage, store, args.limit, args.batch)
    if args.index or do_all:
        build_index(knowledge, usage, store)
    if args.families or do_all:
        generate_families(knowledge)


if __name__ == "__main__":
    main()
