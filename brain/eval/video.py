# -*- coding: utf-8 -*-
"""视频抽帧（ffmpeg 子进程，无 shell、路径白名单）。"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def _find_tool(name: str, env_key: str, win_default: str) -> str:
    """查找链：环境变量 -> PATH（跨平台）-> Windows 默认位置。"""
    env_val = os.environ.get(env_key)
    if env_val:
        return env_val
    found = shutil.which(name)
    if found:
        return found
    return win_default


FFMPEG = _find_tool("ffmpeg", "FFMPEG_PATH",
                    r"D:\ffmpeg-master-latest-win64-gpl-shared\bin\ffmpeg.exe")
FFPROBE = _find_tool("ffprobe", "FFPROBE_PATH",
                     r"D:\ffmpeg-master-latest-win64-gpl-shared\bin\ffprobe.exe")

ALLOWED_EXTS = (".mp4", ".webm", ".mkv", ".mov", ".avi", ".gif")


class VideoError(Exception):
    pass


def _check_path(p: str | Path) -> Path:
    path = Path(p)
    if not path.exists():
        raise VideoError(f"视频不存在: {path}")
    if path.suffix.lower() not in ALLOWED_EXTS:
        raise VideoError(f"不支持的视频格式: {path.suffix}")
    if not Path(FFMPEG).exists() or not Path(FFPROBE).exists():
        raise VideoError(f"未找到 ffmpeg/ffprobe（{FFMPEG}）。"
                         "请设置 FFMPEG_PATH/FFPROBE_PATH 环境变量。")
    return path


def probe_duration(video: str | Path) -> float:
    """ffprobe 取视频时长（秒）。"""
    path = _check_path(video)
    r = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, timeout=60, check=False)
    if r.returncode != 0:
        raise VideoError(f"ffprobe 失败: {r.stderr[:200]}")
    try:
        return float(r.stdout.strip())
    except ValueError:
        raise VideoError(f"无法解析时长: {r.stdout[:100]}")


def sample_frames(video: str | Path, n: int = 4,
                  out_dir: str | Path = None) -> list[str]:
    """均匀抽 n 帧。返回帧 PNG 路径列表。"""
    path = _check_path(video)
    if n < 1 or n > 16:
        raise VideoError(f"帧数 {n} 超出范围 [1,16]")
    duration = probe_duration(path)
    if duration <= 0:
        raise VideoError("视频时长无效")
    out = Path(out_dir or tempfile.mkdtemp(prefix="vframes_"))
    out.mkdir(parents=True, exist_ok=True)

    frames = []
    for i in range(n):
        t = duration * (i + 0.5) / n          # 每段中点，避开黑场首尾
        dest = out / f"frame_{i:02d}.png"
        r = subprocess.run(
            [FFMPEG, "-y", "-ss", f"{t:.3f}", "-i", str(path),
             "-frames:v", "1", "-q:v", "2", str(dest)],
            capture_output=True, text=True, timeout=120, check=False)
        if r.returncode != 0 or not dest.exists():
            raise VideoError(
                f"抽帧失败（t={t:.2f}s）: {r.stderr[-200:]}")
        frames.append(str(dest))
    return frames
