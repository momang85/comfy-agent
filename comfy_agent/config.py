# -*- coding: utf-8 -*-
"""comfy-agent 配置：服务器地址、路径、常量。全部可用环境变量覆盖。"""
import os
from pathlib import Path

# ---- ComfyUI 服务器 ----
COMFY_URL = os.environ.get("COMFY_URL", "http://127.0.0.1:8188").rstrip("/")
# SSRF 防护开关：默认只允许回环地址；=1 时放行私网/公网地址（连接其他机器的 ComfyUI）
COMFY_ALLOW_LAN = os.environ.get("COMFY_ALLOW_LAN", "0") == "1"

# ---- 本机路径（跨设备可移植：环境变量 COMFY_ROOT 优先；未设置时按平台探测）----
def _default_comfy_root() -> str:
    """无环境变量时的 ComfyUI 安装目录兜底。

    核心生成流程走 COMFY_URL（HTTP 连 127.0.0.1:8188），本目录仅用于
    读取 Manager 缓存（节点→包映射）等增强功能，不是硬依赖。"""
    if os.name == "nt":
        # Windows 整合包常见位置（install.bat/一键启动.bat 写 comfy_root.local 覆盖）
        return r"D:\comfiUI\ComfyUI-aki\ComfyUI-aki-v3\ComfyUI"
    for cand in ("ComfyUI", "comfyui", "ComfyUI/ComfyUI"):
        p = Path.home() / cand
        if p.exists():
            return str(p)
    return ""


def _normalize_comfy_root(raw: str) -> Path:
    """COMFY_ROOT 归一为**ComfyUI 安装目录**（其下有 models/ 与 main.py）。

    install.bat/一键启动.bat 存的 comfy_root.local 是**整合包根目录**
    （python\\python.exe 与 ComfyUI\\main.py 在那里），配置读取的是 ComfyUI
    子目录的 models/ 与 user/__manager/cache。两种形态都要认，否则
    MODELS_DIR/MANAGER_CACHE 全指向不存在的路径，相关增强功能静默失效。
    """
    p = Path(raw)
    if (p / "models").is_dir() or (p / "main.py").exists():
        return p
    nested = p / "ComfyUI"
    if (nested / "main.py").exists() or (nested / "models").is_dir():
        return nested
    return p


COMFY_ROOT = _normalize_comfy_root(
    os.environ.get("COMFY_ROOT", _default_comfy_root()))
MODELS_DIR = COMFY_ROOT / "models"
INPUT_DIR = COMFY_ROOT / "input"
OUTPUT_DIR = COMFY_ROOT / "output"
USER_WORKFLOWS_DIR = COMFY_ROOT / "user" / "default" / "workflows"
MANAGER_CACHE = COMFY_ROOT / "user" / "__manager" / "cache"

# ---- 大脑 LLM（OpenAI 兼容 BYOK） ----
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.z.ai/api/paas/v4")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "glm-4.5-air")

# ---- 视觉评估 VLM（OpenAI 兼容） ----
VLM_BASE_URL = os.environ.get("VLM_BASE_URL", LLM_BASE_URL)
VLM_API_KEY = os.environ.get("VLM_API_KEY", LLM_API_KEY)
VLM_MODEL = os.environ.get("VLM_MODEL", "glm-4.6v")


def load_llm_api_key() -> str:
    """API key 加载链：环境变量 -> settings.json -> Windows 注册表 zcode-api-key。
    key 永不写入源码/配置文件（settings.json 为本地用户配置，gitignored）。"""
    global LLM_API_KEY, VLM_API_KEY
    if LLM_API_KEY:
        return LLM_API_KEY
    s = load_user_settings()
    if s.get("llm_api_key") and not LLM_API_KEY:
        LLM_API_KEY = s["llm_api_key"]
        if not VLM_API_KEY:
            VLM_API_KEY = s["llm_api_key"]
        return LLM_API_KEY
    try:
        import winreg
        with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment") as h:
            val, _ = winreg.QueryValueEx(h, "zcode-api-key")
            LLM_API_KEY = val
            if not VLM_API_KEY:
                VLM_API_KEY = val
            return val
    except (OSError, ImportError):
        return ""


def load_user_settings() -> dict:
    """读取本地用户设置（settings.json，gitignored）。不存在返回空 dict。"""
    try:
        if SETTINGS_PATH.exists():
            import json
            return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return {}


def save_user_settings(settings: dict) -> None:
    """写入本地用户设置。"""
    import json
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, ensure_ascii=False, indent=1),
                             encoding="utf-8")

# ---- 评估策略: auto | local | off ----
EVAL_POLICY = os.environ.get("EVAL_POLICY", "auto").lower()

# ---- 工作区（项目根相对，跨设备可移植；AGENT_HOME 环境变量可覆盖） ----
PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGENT_HOME = Path(os.environ.get(
    "AGENT_HOME",
    str(PROJECT_ROOT / ".comfy-agent"),
))
SESSIONS_DIR = AGENT_HOME / "sessions"
KNOWLEDGE_DIR = AGENT_HOME / "knowledge"
RESULTS_DIR = AGENT_HOME / "outputs"
SETTINGS_PATH = AGENT_HOME / "settings.json"

# ---- 常量 ----
HTTP_TIMEOUT = int(os.environ.get("COMFY_HTTP_TIMEOUT", "30"))
POLL_INTERVAL = 2.0          # /history 轮询间隔（秒）
MAX_REPAIR_ATTEMPTS = 3      # 修复重试上限
BATCH_SIZE_LIMIT = 8         # 单次批量上限（12GB 显存保护）
# 世界模型（节点签名/模型清单）快照的新鲜度上限（秒）：超过就重取。
# 旧实现无 TTL，下载了模型/装了节点系统仍用旧镜像判"缺"（项目 5 实证）。
WORLD_SNAPSHOT_TTL = int(os.environ.get("WORLD_SNAPSHOT_TTL", "600"))

for _d in (SESSIONS_DIR, KNOWLEDGE_DIR, RESULTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# 提示词规范：模型家族 -> 文本编码器风格
MODEL_FAMILIES = {
    "novaAnimeXL_ilV180.safetensors": "sdxl",
    "anything-v5.safetensors": "sd15",
    "anything-v5-PrtRE.safetensors": "sd15",
    "ltx-2.3-22b-distilled-1.1.safetensors": "ltx",
    "minimax_h3_fl2va_pruned_int8_convrot.safetensors": "minimax",
}

def family_of(model_name: str) -> str:
    """根据模型文件名推断提示词规范家族。"""
    name = (model_name or "").lower()
    for key, fam in MODEL_FAMILIES.items():
        if key.lower() in name or name in key.lower():
            return fam
    if "xl" in name or "sdxl" in name:
        return "sdxl"
    if "ltx" in name:
        return "ltx"
    if "minimax" in name or "h3" in name:
        return "minimax"
    return "sd15"
