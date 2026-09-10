# -*- coding: utf-8 -*-
"""连线健康审计：回答"系统运行是否能正常连接所有节点"。

三类审计（`python -m comfy_agent.cli health` 输出完整报告）：
  A. 模板/链路审计：全部模板 + 能力接线骨架 render 后过 validate_workflow
     （含新增的连线类型校验），报告每个图的节点数/连线数/问题
  B. 类型覆盖审计：1527 个节点逐一做输出类型推断，量化引擎"能看懂多少
     节点的端口"（可推断比例 + 不可推断样本）
  C. 能力可用性审计：nodes_prefs.capability_table 全表（节点+模型双重核验）

骨架是最小化的标准接线定义；骨架自身定义欠妥也会被如实报告出来。
"""
from __future__ import annotations

from .nodes_prefs import capability_table
from .validate import validate_workflow

SDXL_CKPT = "sdXL\\novaAnimeXL_ilV180.safetensors"
CN_UNION = "controlnet++_union_sdxl_promax.safetensors"
CN_OPENPOSE = "controlnetxlCNXL_2vxpswa7OpenposeV21.safetensors"
CN_DEPTH = "controlnetxlCNXL_bdsqlszDepth.safetensors"
CN_LINEART = "control_v11p_sd15s2_lineart_anime_fp16.safetensors"


def _clip(nid, text):
    return {"class_type": "CLIPTextEncode", "inputs": {"clip": [nid, 1], "text": text}}


def _ks(model, pos, neg, latent, denoise, steps=22, cfg=7.0):
    return {"class_type": "KSampler", "inputs": {
        "model": [model, 0], "positive": [pos, 0], "negative": [neg, 0],
        "latent_image": [latent, 0], "seed": 0, "steps": steps, "cfg": cfg,
        "sampler_name": "euler", "scheduler": "karras", "denoise": denoise}}


def _hiresfix():
    """市场级 t2i 骨架：潜空间 1.5x hiresfix + 二段低 denoise 重采样。"""
    return {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": SDXL_CKPT}},
        "2": _clip("1", "masterpiece, best quality, 1girl"),
        "3": _clip("1", "(worst quality, low quality:1.4), bad anatomy"),
        "4": {"class_type": "EmptyLatentImage",
              "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
        "5": _ks("1", "2", "3", "4", 1.0, 22, 7.0),
        "6": {"class_type": "LatentUpscaleBy", "inputs": {
            "samples": ["5", 0], "upscale_method": "bicubic", "scale_by": 1.5}},
        "7": _ks("1", "2", "3", "6", 0.4, 14, 7.0),
        "8": {"class_type": "VAEDecode",
              "inputs": {"samples": ["7", 0], "vae": ["1", 2]}},
        "9": {"class_type": "SaveImage",
              "inputs": {"images": ["8", 0], "filename_prefix": "audit_hires"}},
    }


def _inpaint():
    """局部重绘骨架：VAEEncodeForInpaint + 遮罩限定的低 denoise 采样。"""
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": "example.png"}},
        "2": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": SDXL_CKPT}},
        "3": _clip("2", "clean background, masterpiece"),
        "4": _clip("2", "low quality, blurry"),
        "5": {"class_type": "VAEEncodeForInpaint", "inputs": {
            "pixels": ["1", 0], "vae": ["2", 2], "mask": ["1", 1],
            "grow_mask_by": 6}},
        "6": _ks("2", "3", "4", "5", 0.6, 20, 7.0),
        "7": {"class_type": "VAEDecode",
              "inputs": {"samples": ["6", 0], "vae": ["2", 2]}},
        "8": {"class_type": "SaveImage",
              "inputs": {"images": ["7", 0], "filename_prefix": "audit_inpaint"}},
    }


def _controlnet(preproc: dict, cn_file: str, strength=0.7, denoise=0.8):
    """ControlNet 骨架：预处理 → ControlNet → 正负条件 → 采样。"""
    wf = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "example.png"}},
        "2": preproc,
        "3": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": SDXL_CKPT}},
        "4": _clip("3", "masterpiece, best quality, 1girl"),
        "5": _clip("3", "(worst quality, low quality:1.4), bad anatomy"),
        "6": {"class_type": "ControlNetLoader",
              "inputs": {"control_net_name": cn_file}},
        "7": {"class_type": "ControlNetApplyAdvanced", "inputs": {
            "positive": ["4", 0], "negative": ["5", 0],
            "control_net": ["6", 0], "image": ["2", 0],
            "strength": strength, "start_percent": 0.0, "end_percent": 1.0}},
        "8": {"class_type": "VAEEncode",
              "inputs": {"pixels": ["1", 0], "vae": ["3", 2]}},
        "9": _ks("3", "7", "7", "8", denoise, 25, 7.0),
        "10": {"class_type": "VAEDecode",
               "inputs": {"samples": ["9", 0], "vae": ["3", 2]}},
        "11": {"class_type": "SaveImage",
               "inputs": {"images": ["10", 0], "filename_prefix": "audit_cn"}},
    }
    return wf


SKELETONS: dict[str, callable] = {
    "hiresfix": _hiresfix,
    "inpaint": _inpaint,
    "canny_cn": lambda: _controlnet(
        {"class_type": "Canny", "inputs": {
            "image": ["1", 0], "low_threshold": 0.3, "high_threshold": 0.7}},
        CN_UNION),
    "pose_cn": lambda: _controlnet(
        {"class_type": "OpenposePreprocessor", "inputs": {
            "image": ["1", 0], "detect_hand": "enable",
            "detect_body": "enable", "detect_face": "enable"}},
        CN_OPENPOSE),
    "depth_cn": lambda: _controlnet(
        {"class_type": "DepthAnythingPreprocessor",
         "inputs": {"image": ["1", 0]}},
        CN_DEPTH),
    "lineart_cn": lambda: _controlnet(
        {"class_type": "LineartStandardPreprocessor",
         "inputs": {"image": ["1", 0]}},
        CN_LINEART),
}


def _count_links(wf: dict) -> int:
    return sum(1 for n in wf.values()
               for v in n.get("inputs", {}).values()
               if isinstance(v, list) and len(v) == 2
               and isinstance(v[0], str))


def _audit_templates(knowledge) -> list[dict]:
    from .templates import all_templates
    rows = []
    for t in all_templates():
        # 必填参数给类型适配的哑值（image/video 用占位文件名，引擎对
        # 运行时上传类文件输入本来就不做阻断检查）
        params = {"prompt": "audit test subject"}
        for prm in t.params():
            if prm.required and prm.name not in params:
                params[prm.name] = {"image": "example.png",
                                    "video": "a.mp4"}.get(
                    prm.ptype,
                    "x" if prm.ptype == "str" else
                    1 if prm.ptype == "int" else
                    0.5 if prm.ptype == "float" else
                    (prm.choices[0] if prm.choices else "x"))
        try:
            wf = t.render(params)
            issues = [i.to_dict() for i in validate_workflow(wf, knowledge)]
        except Exception as e:
            rows.append({"template": t.id, "ok": False,
                         "error": str(e)[:160]})
            continue
        rows.append({"template": t.id, "ok": not issues,
                     "nodes": len(wf), "links": _count_links(wf),
                     "issues": [i["message"] for i in issues[:5]]})
    return rows


def _audit_skeletons(knowledge) -> list[dict]:
    rows = []
    for name, build in SKELETONS.items():
        try:
            wf = build()
            issues = [i.to_dict() for i in validate_workflow(wf, knowledge)]
        except Exception as e:
            rows.append({"skeleton": name, "ok": False,
                         "error": str(e)[:160]})
            continue
        rows.append({"skeleton": name, "ok": not issues,
                     "nodes": len(wf), "links": _count_links(wf),
                     "issues": [i["message"] for i in issues[:5]]})
    return rows


def _audit_type_coverage(knowledge) -> dict:
    """1527 节点的输出类型可推断性：引擎建图时'看得懂'的端口比例。

    不可推断的多为保存类/训练类终端节点（本就没有输出，不可能作为
    连线源），属正常情况而非故障。"""
    from .synth.graph import Graph
    total = resolvable = 0
    bad: list[tuple[str, str]] = []
    for cls, info in knowledge.snapshot.items():
        total += 1
        outs = info.get("output") or []
        if not outs:
            bad.append((cls, "无 output 声明（终端/保存类，属正常）"))
            continue
        g = Graph({"1": {"class_type": cls, "inputs": {}}}, knowledge)
        if all(g.output_type("1", slot) is not None
               for slot in range(len(outs))):
            resolvable += 1
        else:
            bad.append((cls, "输出槽类型推断失败"))
    return {"total": total, "resolvable": resolvable,
            "unresolvable": len(bad),
            "ratio": round(resolvable / total, 4) if total else 0,
            "samples": [{"class": c, "reason": r} for c, r in bad[:15]]}


def audit_all(knowledge=None) -> dict:
    from .knowledge import Knowledge
    knowledge = knowledge or Knowledge.build()
    return {
        "ok": True,
        "templates": _audit_templates(knowledge),
        "skeletons": _audit_skeletons(knowledge),
        "type_coverage": _audit_type_coverage(knowledge),
        "capabilities": capability_table(knowledge),
    }
