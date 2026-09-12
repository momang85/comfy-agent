# -*- coding: utf-8 -*-
"""失败分类 → 允许的动作。把"失败"从一句话变成可执行的分支决策。

项目 5 里所有失败都被当成同一类处理，于是：
- 视觉 400（配置错）被当成"换个工具继续"，编了画面内容（22:40）
- 缺加载节点被当成"缺模型"，白下 21.46MB（23:52-23:55）
- 语义不达标被当成"改 denoise 重掷"，三次整图重绘（23:50-23:52）

分类依据尽量用**结构化字段**（stage/错误码/工具返回），不靠自然语言猜测；
分类结果决定动作，动作是白名单，避免模型自由发挥。
"""
from __future__ import annotations

import re

# 失败类别
CONFIG = "config"            # 配置/鉴权：模型名错、key 错、地址错 → 停下告知
MISSING_ASSET = "missing_asset"    # 缺模型文件 → 解析/下载，绝不重绘
MISSING_NODE = "missing_node"      # 缺节点/依赖 → 说明+替代链路，下载无用
TRANSIENT = "transient"            # 瞬时网络/服务抖动 → 退避重试 1 次
SEMANTIC = "semantic"              # 执行成功但不达标 → 换手法
BUDGET = "budget"                  # 超预算/无提升 → 停并汇报
USER_INPUT = "user_input"          # 缺用户输入（遮罩/描述/选择）→ 问用户
UNKNOWN = "unknown"

#: 每类失败允许的动作白名单（引擎与提示词共同遵守）
ACTIONS: dict[str, list[str]] = {
    CONFIG: ["stop_and_tell_user", "fix_settings"],
    MISSING_ASSET: ["resolve_asset", "download_or_ask", "degrade"],
    MISSING_NODE: ["explain_missing_node", "use_alternative_route", "ask_user"],
    TRANSIENT: ["retry_once"],
    SEMANTIC: ["change_method", "ask_user"],
    BUDGET: ["stop_and_report"],
    USER_INPUT: ["ask_user"],
    UNKNOWN: ["inspect", "ask_user"],
}

_HINTS: list[tuple[str, str]] = [
    # 配置/鉴权（先判：这类重试永远不会成功）
    (r"unknown model|model_not_available|invalid.*api.?key|unauthorized|401|"
     r"模型不可用|model_capability_not_supported|不支持该能力", CONFIG),
    (r"未配置.*key|未配置视觉|api_key", CONFIG),
    # 缺节点/依赖（比缺模型更该先判：下了也没用）
    (r"本机没有节点|node.*not (found|exist)|缺少节点|no such node", MISSING_NODE),
    (r"cannot import|importerror|module.*not found|依赖", MISSING_NODE),
    # 缺资产
    (r"缺少模型|missing model|value not in list|not in choices|"
     r"checkpoint.*not found|找不到模型", MISSING_ASSET),
    # 瞬时
    (r"http 5\d\d|bad gateway|service_busy|timeout|timed out|connection|"
     r"连接中断|无法连接|eof|ssl|remote end closed", TRANSIENT),
    # 语义
    (r"不达标|verdict.*false|pass.*false|score", SEMANTIC),
]


def classify(text: str = "", *, stage: str = "", extra: str = "") -> str:
    """把一段错误文本/阶段名归类。结构化信号优先于文本。"""
    if stage in ("render_failed", "validation_failed", "repair_failed"):
        blob = f"{text} {extra}"
        if re.search(r"缺少模型|not in choices|value not in list", blob, re.I):
            return MISSING_ASSET
        return UNKNOWN
    blob = f"{text} {extra}".strip()
    if not blob:
        return UNKNOWN
    for pattern, kind in _HINTS:
        if re.search(pattern, blob, re.IGNORECASE):
            return kind
    return UNKNOWN


def policy_for(kind: str) -> dict:
    """该类失败允许的动作 + 一句给用户的说明口径。"""
    actions = ACTIONS.get(kind, ACTIONS[UNKNOWN])
    return {"kind": kind, "actions": actions,
            "may_rerender": kind in (TRANSIENT,),
            "must_change_method": kind == SEMANTIC,
            "download_helps": kind == MISSING_ASSET,
            "tell_user": kind in (CONFIG, MISSING_NODE, BUDGET, USER_INPUT)}


def describe(kind: str, detail: str = "") -> str:
    """统一口径的失败说明（避免"服务端临时问题"这类误诊）。"""
    table = {
        CONFIG: "接口/模型配置不对（重试无用）：请到 ⚙ 检查地址、Key 与模型名",
        MISSING_ASSET: "缺少模型文件：可搜索下载或换等价链路，不要重绘",
        MISSING_NODE: "缺的是**节点或依赖**，不是模型：下载模型解决不了",
        TRANSIENT: "服务商瞬时故障：可退避重试一次",
        SEMANTIC: "执行成功但不满足要求：必须换手法，禁止只调参数重掷",
        BUDGET: "已达尝试/预算上限：停止并如实汇报现状",
        USER_INPUT: "需要用户输入（遮罩/文字描述/选择）才能继续",
    }
    base = table.get(kind, "未归类的失败：先查清原因再动手")
    return f"{base}{('（' + detail[:120] + '）') if detail else ''}"
