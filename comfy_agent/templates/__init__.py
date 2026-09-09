# -*- coding: utf-8 -*-
"""模板注册表：统一入口。"""
from __future__ import annotations

from .image import TEMPLATES_IMAGE
from .video import TEMPLATES_VIDEO

_ALL = TEMPLATES_IMAGE + TEMPLATES_VIDEO


def all_templates() -> list:
    return _ALL


def get_template(tid: str):
    for t in _ALL:
        if t.id == tid:
            return t
    return None


def catalog() -> list[dict]:
    return [t.to_dict() for t in _ALL]
