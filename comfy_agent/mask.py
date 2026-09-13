# -*- coding: utf-8 -*-
"""遮罩工具（纯标准库）：读遮罩 PNG 并回答"这块遮罩有效吗"。

为什么需要：局部修复依赖遮罩，而"检测不到目标"（手不在画面里/被裁掉）会
得到一张全黑遮罩 —— 此时采样等于没修，若直接交付就是**假装修好了**（比失败
更糟）。同理遮罩过大（接近整图）说明这不是局部修复，应当拦下。

只支持 8-bit PNG（灰度/RGB/RGBA，非隔行）：ComfyUI 的 MaskToImage 输出即此类。
遇到不支持的形态返回 None，调用方据此**跳过判定**（不阻断修复）。
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

#: 覆盖率低于此值视为"没检测到目标"（遮罩几乎为空）
MIN_COVERAGE = 0.005
#: 高于此值视为"等于整图重绘"，不是局部修复
MAX_COVERAGE = 0.60


def _read_png(path: str | Path):
    """返回 (width, height, gray_rows)；不支持则 None。

    gray_rows: [[0..255 逐像素灰度], ...]（取 RGB 均值/alpha 不为 0 处为白）
    """
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return None
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    pos, w, h, depth, ctype, interlace, idat = 8, 0, 0, 0, 0, 0, b""
    while pos + 8 <= len(raw):
        (ln,) = struct.unpack(">I", raw[pos:pos + 4])
        tag = raw[pos + 4:pos + 8]
        data = raw[pos + 8:pos + 8 + ln]
        pos += 12 + ln
        if tag == b"IHDR":
            w, h, depth, ctype, _comp, _filt, interlace = struct.unpack(
                ">IIBBBBB", data[:13])
        elif tag == b"IDAT":
            idat += data
        elif tag == b"IEND":
            break
    if not w or not h or depth != 8 or interlace != 0:
        return None
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(ctype)
    if channels is None:
        return None
    try:
        blob = zlib.decompress(idat)
    except zlib.error:
        return None
    stride = w * channels
    rows, prev, off = [], bytearray(stride), 0
    for _y in range(h):
        if off + 1 + stride > len(blob):
            break
        ftype = blob[off]
        line = bytearray(blob[off + 1:off + 1 + stride])
        off += 1 + stride
        # PNG 逐行滤波还原（只需要 Sub/Up/Average/Paeth 的行内运算）
        for i in range(stride):
            a = line[i - channels] if i >= channels else 0
            b = prev[i]
            c = prev[i - channels] if i >= channels else 0
            x = line[i]
            if ftype == 1:
                x = (x + a) & 0xFF
            elif ftype == 2:
                x = (x + b) & 0xFF
            elif ftype == 3:
                x = (x + ((a + b) >> 1)) & 0xFF
            elif ftype == 4:
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                x = (x + pr) & 0xFF
            line[i] = x
        px = []
        for x_i in range(w):
            base = x_i * channels
            if channels == 1:
                v = line[base]
            elif channels == 2:
                v = line[base] if line[base + 1] > 8 else 0
            elif channels == 3:
                v = (line[base] + line[base + 1] + line[base + 2]) // 3
            else:
                v = ((line[base] + line[base + 1] + line[base + 2]) // 3
                     if line[base + 3] > 8 else 0)
            px.append(v)
        rows.append(px)
        prev = line
    if not rows:
        return None
    return w, h, rows


def mask_coverage(path: str | Path) -> float | None:
    """遮罩中"白"的像素占比（0-1）；读不了返回 None（调用方跳过判定）。"""
    got = _read_png(path)
    if got is None:
        return None
    w, h, rows = got
    total = w * h
    if not total:
        return None
    white = sum(1 for row in rows for v in row if v >= 128)
    return white / total


def bbox(path: str | Path) -> tuple[int, int, int, int] | None:
    """遮罩的包围盒 (x0, y0, x1, y1)（含端点）；无白色或读不了返回 None。"""
    got = _read_png(path)
    if got is None:
        return None
    w, h, rows = got
    x0, y0, x1, y1 = w, h, -1, -1
    for y, row in enumerate(rows):
        for x, v in enumerate(row):
            if v >= 128:
                x0, y0 = min(x0, x), min(y0, y)
                x1, y1 = max(x1, x), max(y1, y)
    if x1 < 0:
        return None
    return x0, y0, x1, y1


def image_size(path: str | Path) -> tuple[int, int] | None:
    """读图片尺寸 (w, h)：PNG 走 zlib 解析，JPEG 找 SOF 段；读不了返回 None。

    用途：矩形遮罩必须按**真实图像尺寸**生成——按固定 1024 建遮罩再被
    ComfyUI 缩放，区域会偏移（实测遮罩外像素也被改动）。
    """
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return None
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        got = _read_png(path)
        return (got[0], got[1]) if got else None
    if raw[:2] == b"\xff\xd8":                      # JPEG：扫 SOF0/1/2/… 段
        i = 2
        while i + 9 < len(raw):
            if raw[i] != 0xFF:
                i += 1
                continue
            marker = raw[i + 1]
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            seg_len = struct.unpack(">H", raw[i + 2:i + 4])[0]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                          0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", raw[i + 5:i + 9])
                return (w, h)
            i += 2 + seg_len
    return None


def check_mask(path: str | Path) -> dict:
    """遮罩可用性判定：{ok, coverage, reason, hint}。

    coverage=None 表示读不了（非 8-bit PNG 等）→ ok=True 但注明未判定，
    避免因为解析能力不足而阻断正常修复。
    """
    cov = mask_coverage(path)
    if cov is None:
        return {"ok": True, "coverage": None, "checkable": False,
                "reason": "", "hint": "遮罩无法解析（非 8-bit PNG），跳过有效性判定"}
    if cov < MIN_COVERAGE:
        return {"ok": False, "coverage": cov, "checkable": True,
                "reason": f"遮罩几乎是空的（覆盖率 {cov:.3%}）：没有检测到要修的目标",
                "hint": ("如实告诉用户「没定位到要修的区域」并请其上传黑白遮罩"
                         "（白色=要重绘的区域），不要声称已经修好；"
                         "若目标其实在画面外，请换图或改需求")}
    if cov > MAX_COVERAGE:
        return {"ok": False, "coverage": cov, "checkable": True,
                "reason": f"遮罩覆盖 {cov:.1%}，接近整图：这不是局部修复",
                "hint": ("这等于重绘整图，违背「局部问题走局部」；"
                         "请缩小 target 范围（hand/face/box），"
                         "或告诉用户需要整图重做并说明代价")}
    return {"ok": True, "coverage": cov, "checkable": True, "reason": "",
            "hint": ""}
