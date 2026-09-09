# -*- coding: utf-8 -*-
"""Friend-mode 仿真：模拟他人全新设备首次使用。

- 全新临时 AGENT_HOME（空项目/空技能/空历史）
- COMFY_ROOT 走环境变量（模拟朋友的 ComfyUI 路径）
- API key 通过前端 /api/settings 接口配置（不走注册表）
- Web UI 起在独立端口，提交小任务 → 真实出图

运行：python scripts/friend_mode_test.py
"""
import ipaddress
import json
import os
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

_HOST, _PORT = "127.0.0.1", 8900


def _base_url() -> str:
    """构造并校验本地测试服务基址（仅回环，同 run_report.py 策略）。"""
    u = urllib.parse.urlparse(f"http://{_HOST}:{_PORT}")
    assert u.scheme == "http", "仅允许 http"
    ip = ipaddress.ip_address(u.hostname or "")
    assert ip.is_loopback, "仅允许回环地址"
    return f"http://{u.hostname}:{u.port}"


BASE = _base_url()


def _open(path, data=None, method="GET", timeout=30):
    assert path.startswith("/") and ".." not in path, "非法路径"
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout)


def post(path, body):
    with _open(path, data=json.dumps(body).encode(), method="POST") as r:
        return json.loads(r.read())


def get(path):
    with _open(path) as r:
        return json.loads(r.read())


# 1) 全新临时 AGENT_HOME（他人第一次使用的状态）
TMP = Path(tempfile.mkdtemp(prefix="friend_mode_"))
os.environ["AGENT_HOME"] = str(TMP)
# 2) COMFY_ROOT 环境变量（朋友配置的路径——此处指向本机真实 ComfyUI 作仿真）
if "COMFY_ROOT" not in os.environ:
    os.environ["COMFY_ROOT"] = r"D:\comfiUI\ComfyUI-aki\ComfyUI-aki-v3\ComfyUI"

from comfy_agent import config  # noqa: E402

assert str(config.AGENT_HOME).startswith(str(TMP)), "AGENT_HOME 未走环境变量"
print(f"[1] 全新 AGENT_HOME: {config.AGENT_HOME}")

from brain.web.server import serve  # noqa: E402

t = threading.Thread(target=serve, kwargs={"port": _PORT,
                                           "open_browser": False},
                     daemon=True)
t.start()
time.sleep(3)

# 3) 通过前端设置接口配置 key（模拟朋友在 ⚙ 面板粘贴自己的 key）
registry_key = config.load_llm_api_key() or os.environ.get("LLM_API_KEY", "")
assert registry_key, "测试需要可用的 key（从注册表/环境读取后经 settings 接口注入）"
saved = post("/api/settings", {"llm_api_key": registry_key,
                               "llm_base_url": "https://api.z.ai/api/paas/v4",
                               "llm_model": "glm-4.5-air",
                               "vlm_model": "glm-4.6v"})
assert saved.get("ok"), saved
masked = saved["effective"]["api_key_masked"]
assert "…" in masked or masked == "已设置", masked
assert registry_key not in json.dumps(saved), "key 明文回传泄漏！"
print(f"[2] settings 接口保存成功，返回脱敏: {masked}")

# 4) settings.json 落盘在临时 AGENT_HOME（不在项目目录）
sf = config.SETTINGS_PATH
assert sf.exists() and str(sf).startswith(str(TMP)), "settings.json 未落在临时目录"
print(f"[3] settings.json 位置: {sf}")

# 5) 提交小任务 → 真实出图
post("/api/message", {"text": "画一只戴着红色围巾的小狐狸，512x512"})
print("[4] 任务已提交，等待出图（最长 300s）...")
deadline = time.time() + 300
delivered = False
while time.time() < deadline:
    st = get("/api/status")
    outs = st.get("outputs", [])
    if outs and any(o.get("new") for o in outs):
        delivered = True
        break
    time.sleep(5)

assert delivered, "Friend-mode 出图失败"
print("[5] 出图成功，产物在临时 AGENT_HOME 项目目录:")
for o in get("/api/status")["outputs"][:2]:
    print("   ", o["name"])

# 6) 清理检查：项目源码目录无 friend 数据写入
assert not (PROJ / "settings.json").exists()
print("[6] 项目目录未被写入任何 friend 配置 ✓")

# 7) 模型自动适配仿真：朋友设备没有开发机的默认模型（novaAnimeXL），
#    只有一个任意 SD1.5 模型 -> t2i 应自动绑定它并收敛分辨率到 512
from comfy_agent.knowledge import Knowledge            # noqa: E402
from comfy_agent.model_adapt import adapt_ckpt         # noqa: E402
from comfy_agent.templates.image import T2I, SDXL_CKPT  # noqa: E402

real = Knowledge.build()
friend_k = Knowledge(
    snapshot=real.snapshot,
    models={**real.models,
            "checkpoints": ["anything-v5-PrtRE.safetensors"]})
params, notes = adapt_ckpt(T2I(SDXL_CKPT), {}, friend_k)
assert params.get("ckpt") == "anything-v5-PrtRE.safetensors", params
assert params.get("width") == 512 and params.get("height") == 512, params
assert notes and "自动适配" in notes[0], notes
print(f"[7] 缺 novaAnimeXL 的设备：t2i 自动适配为 {params['ckpt']}"
      f"（分辨率收敛 512）✓")

# 8) model_prefs 经前端设置接口保存（朋友指定自己的偏好模型）
post("/api/settings", {"model_prefs": {"sd15": "anything-v5-PrtRE.safetensors"}})
s = config.load_user_settings()
assert s.get("model_prefs", {}).get("sd15") == "anything-v5-PrtRE.safetensors"
print("[8] model_prefs 经 /api/settings 保存并生效 ✓")

print("\n=== Friend-mode 仿真通过：他人设备可用 ===")
