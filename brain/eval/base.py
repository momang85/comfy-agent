# -*- coding: utf-8 -*-
"""分层评估子系统。

策略（EVAL_POLICY 环境变量，默认 auto）：
  auto : Tier0 确定性门槛每次跑（免费）→ 最终成图走 Tier2 云端 VLM；
         批量任务 Tier0 过滤 + 抽检
  local: Tier0 + 本机 VLM（Phase 2 扩展槽，当前回退为仅 Tier0）
  off  : 不自动评估（用户反馈驱动）

视频评估：抽帧 4 张走同一管线。
"""
from __future__ import annotations

import struct
from pathlib import Path

from comfy_agent import config


class EvalResult:
    def __init__(self):
        self.tier0: list[dict] = []     # 确定性检查结果
        self.vlm: list[dict] = []       # VLM 评估结果
        self.retry_advice: str = ""     # 给重试循环的建议（Tier0 不达标时）
        # 视觉不可用时如实记录：曾经只写 pass=None（ok 属性不算失败），
        # 表现成"评估一切正常"，而实际语义检查全部被跳过
        self.vlm_error: str = ""

    @property
    def ok(self) -> bool:
        t0_fail = [c for c in self.tier0 if not c.get("pass")]
        vlm_fail = [c for c in self.vlm
                    if c.get("pass") is False]
        return not t0_fail and not vlm_fail

    def to_dict(self):
        return {"tier0": self.tier0, "vlm": self.vlm,
                "ok": self.ok, "advice": self.retry_advice,
                "vlm_error": self.vlm_error}


# 视觉不可用只提醒一次（按 地址|模型|错误摘要 去重，避免每张图刷屏）
_VISION_WARNED: set = set()


def _warn_vision_unavailable(err: str) -> None:
    """把"视觉不可用"推成一条 warning：评估静默跳过比失败更危险。"""
    try:
        from ..llm import VLMClient
        eff = VLMClient().effective()
    except Exception:
        eff = {}
    key = f"{eff.get('base_url')}|{eff.get('model')}|{err[:60]}"
    if key in _VISION_WARNED:
        return
    _VISION_WARNED.add(key)
    msg = (f"视觉评估不可用（{eff.get('model') or '?'} @ "
           f"{eff.get('base_url') or '?'}）：{err[:160]}。"
           "图像/视频评估的语义检查已跳过，只有几何检查在生效；"
           "请在 ⚙ 设置里填一个可用的视觉模型（地址/Key 默认跟随大脑）。")
    try:
        from ..events import emit
        emit("stage", {"stage": "warning", "detail": {"warning": msg}})
    except Exception:
        pass


# ---------------- Tier 0: 确定性检查（免费，纯标准库） ----------------

def tier0_check(image_path: str | Path, criteria: dict | None = None) -> dict:
    """确定性门槛：分辨率/完整性/格式。
    criteria: {"min_width": 512, "expect_faces": 1}（人脸检测 Phase 2 接入
    Impact Pack 工作流后启用；纯标准库先做几何检查）。"""
    p = Path(image_path)
    checks = {"file": str(p)}
    if not p.exists():
        checks["pass"] = False
        checks["reason"] = "文件不存在"
        return checks
    try:
        w, h = _png_or_jpeg_size(p)
        checks["width"], checks["height"] = w, h
    except Exception as e:
        checks["pass"] = False
        checks["reason"] = f"无法读取图片尺寸: {e}"
        return checks
    crit = criteria or {}
    problems = []
    if crit.get("min_width") and w < crit["min_width"]:
        problems.append(f"宽度 {w} < 要求 {crit['min_width']}")
    if crit.get("min_height") and h < crit["min_height"]:
        problems.append(f"高度 {h} < 要求 {crit['min_height']}")
    if crit.get("min_pixels") and w * h < crit["min_pixels"]:
        problems.append(f"总像素 {w*h} < 要求 {crit['min_pixels']}")
    checks["pass"] = not problems
    checks["problems"] = problems
    return checks


def _png_or_jpeg_size(p: Path) -> tuple[int, int]:
    """纯标准库读图片尺寸（PNG + JPEG）。"""
    head = p.read_bytes()[:64]
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        w, h = struct.unpack(">II", p.read_bytes()[16:24])
        return w, h
    if head.startswith(b"\xff\xd8"):
        # JPEG: 扫描 SOF 段
        data = p.read_bytes()
        i = 2
        while i < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return w, h
            seg_len = struct.unpack(">H", data[i + 2:i + 4])[0]
            i += 2 + seg_len
    raise ValueError("仅支持 PNG/JPEG")


# ---------------- 策略路由 ----------------

def evaluate(image_paths: list[str], criteria: str = "",
             policy: str = None, sample: int | None = None) -> EvalResult:
    """评估入口。criteria 是自然语言要求（给 VLM）。
    sample: 批量时只抽检前 N 张走 VLM（None=全部）。"""
    policy = (policy or config.EVAL_POLICY).lower()
    result = EvalResult()
    if policy == "off" or not image_paths:
        return result

    # Tier 0：每张都查（免费）
    for ip in image_paths:
        result.tier0.append(tier0_check(ip))

    t0_fail = [c for c in result.tier0 if not c.get("pass")]
    if t0_fail:
        result.retry_advice = "；".join(
            f"{c.get('file')}: {c.get('problems') or c.get('reason')}"
            for c in t0_fail)
        # Tier0 都不过就不浪费 VLM 调用
        return result

    if policy == "local":
        # Phase 2: 本机 Qwen2-VL/Gemma 评估。当前降级为仅 Tier0。
        return result

    # auto: 云端 VLM 评估（批量时抽检）
    to_check = image_paths if sample is None else image_paths[:sample]
    try:
        from ..llm import VLMClient
        vlm = VLMClient()
        if not vlm.ready:
            result.vlm_error = "未配置视觉 API Key"
            result.vlm.append({"pass": None,
                               "note": "VLM_API_KEY 未配置，跳过语义评估"})
            _warn_vision_unavailable(result.vlm_error)
            return result
        for ip in to_check:
            r = vlm.judge_json(ip, criteria or "图像质量良好，无明显畸形")
            r["file"] = ip
            # 区域级诊断提取（给大脑做参数映射）
            if isinstance(r.get("issues"), list) and r["issues"] and \
                    isinstance(r["issues"][0], str):
                r["issues"] = [{"location": "", "description": s, "fix_hint": ""}
                               for s in r["issues"]]
            result.vlm.append(r)
    except Exception as e:
        result.vlm_error = str(e)[:200]
        result.vlm.append({"pass": None, "error": result.vlm_error})
        _warn_vision_unavailable(result.vlm_error)
    return result


def evaluate_video(video_path: str, criteria: str = "",
                   policy: str = None, frames: int = 4) -> EvalResult:
    """视频评估：ffmpeg 抽帧 -> 每帧走图像管线（Tier0 + 云端VLM）-> 聚合。"""
    policy = (policy or config.EVAL_POLICY).lower()
    result = EvalResult()
    if policy == "off":
        return result
    try:
        from .video import sample_frames
        frame_paths = sample_frames(video_path, n=frames)
        result.tier0.append({"file": video_path, "pass": True,
                             "frames_sampled": len(frame_paths)})
    except Exception as e:
        result.tier0.append({"file": video_path, "pass": False,
                             "reason": f"抽帧失败: {str(e)[:200]}"})
        result.retry_advice = "视频抽帧失败，检查 ffmpeg 配置与视频文件"
        return result

    if policy == "local":
        # Phase 2 扩展槽：本机 VLM；当前仅帧级 Tier0
        for fp in frame_paths:
            result.tier0.append(tier0_check(fp))
        return result

    # auto: 帧级 Tier0 + 云端 VLM
    try:
        from ..llm import VLMClient
        vlm = VLMClient()
        if not vlm.ready:
            result.vlm_error = "未配置视觉 API Key"
            result.vlm.append({"pass": None,
                               "note": "VLM_API_KEY 未配置，跳过语义评估"})
            _warn_vision_unavailable(result.vlm_error)
            return result
        for i, fp in enumerate(frame_paths):
            result.tier0.append(tier0_check(fp))
            r = vlm.judge_json(
                fp, criteria or "视频帧质量良好，主体清晰无明显畸形")
            r["file"] = fp
            r["frame_index"] = i
            result.vlm.append(r)
        fails = [v for v in result.vlm if v.get("pass") is False]
        if fails:
            result.retry_advice = (
                f"{len(fails)}/{len(frame_paths)} 帧不达标；"
                + "；".join(
                    f"第{v['frame_index'] + 1}帧: "
                    f"{'; '.join(i.get('description', '') for i in v.get('issues', []))[:120]}"
                    for v in fails))
    except Exception as e:
        result.vlm_error = str(e)[:200]
        result.vlm.append({"pass": None, "error": result.vlm_error})
        _warn_vision_unavailable(result.vlm_error)
    return result
