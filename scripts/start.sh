#!/usr/bin/env bash
# ============================================================
#  ComfyUI Brain One-Click Launcher (coexistence mode)  ·  macOS / Linux
#
#  Default:
#    - ComfyUI already on 8188?  -> reuse it, never kill.
#    - Web UI already on 8899?   -> reuse it.
#    - Missing parts are started fresh (waits until ready).
#
#  Full restart (fresh everything):
#    ./scripts/start.sh restart
#
#  VRAM mode: default = ComfyUI dynamic VRAM (safe for large video models).
#  COMFY_VRAM_MODE=high ./scripts/start.sh  -> pin models in VRAM
#  (image-only; CRASHES with ~20GB video models such as MiniMax H3).
# ============================================================
set -uo pipefail
PROJ="$(cd "$(dirname "$0")/.." && pwd)"
AGENT_HOME="$PROJ/.comfy-agent"
LOG_DIR="$AGENT_HOME/logs"
mkdir -p "$LOG_DIR"

COMFY_ROOT="${COMFY_ROOT:-}"
if [ -z "$COMFY_ROOT" ] && [ -f "$PROJ/comfy_root.local" ]; then
  COMFY_ROOT="$(head -n1 "$PROJ/comfy_root.local")"
fi
if [ -z "$COMFY_ROOT" ]; then
  for cand in "$HOME/ComfyUI" "$HOME/comfyui" "/opt/ComfyUI" "$HOME/ComfyUI-aki-v3"; do
    [ -f "$cand/ComfyUI/main.py" ] && { COMFY_ROOT="$cand"; echo "  [probe] found ComfyUI: $cand"; break; }
  done
fi
if [ -z "${COMFY_ROOT:-}" ]; then
  echo "  [ERROR] ComfyUI path not configured. Run scripts/install.sh once, or set COMFY_ROOT."
  exit 1
fi
COMFY_ROOT="${COMFY_ROOT%/}"
if [ ! -f "$COMFY_ROOT/ComfyUI/main.py" ]; then
  echo "  [ERROR] invalid ComfyUI path: $COMFY_ROOT (expected ComfyUI/main.py under it)"
  exit 1
fi
# python 选择链：便携包 -> venv -> 系统 python3
for cand in "$COMFY_ROOT/python/bin/python3" "$COMFY_ROOT/venv/bin/python" "$COMFY_ROOT/venv/bin/python3" "$(command -v python3 || true)"; do
  [ -n "$cand" ] && [ -x "$cand" ] && { PY="$cand"; break; }
done
if [ -z "${PY:-}" ]; then echo "  [ERROR] no usable python found"; exit 1; fi

comfy_alive() {
  curl -sf -m 3 "http://127.0.0.1:8188/system_stats" 2>/dev/null | grep -q comfyui_version
}
web_alive() {
  curl -sf -m 3 "http://127.0.0.1:8899/api/status" 2>/dev/null | grep -q '"ok"'
}
rotate_log() {  # 日志 >5MB 轮转为 .1
  local f="$1"
  if [ -f "$f" ] && [ "$(wc -c <"$f")" -gt 5242880 ]; then
    rm -f "$f.1"; mv "$f" "$f.1"
  fi
}
kill_port() {  # 杀掉监听端口的进程（仅 restart 模式）
  local pids
  pids="$(lsof -ti tcp:"$1" -sTCP:LISTEN 2>/dev/null || true)"
  [ -n "$pids" ] && { echo "$pids" | xargs kill -9 2>/dev/null; echo "  [$2] stopped pid(s) $pids"; }
  return 0
}

if [ "${1:-}" = "restart" ]; then
  echo "============================================"
  echo " Restart mode: stop everything, then start fresh"
  echo "============================================"
  if [ -f "$AGENT_HOME/webui.pid" ]; then
    oldpid="$(head -n1 "$AGENT_HOME/webui.pid" 2>/dev/null || true)"
    [ -n "$oldpid" ] && kill "$oldpid" 2>/dev/null && echo "  [Web UI] stopped old pid=$oldpid"
    rm -f "$AGENT_HOME/webui.pid"
  fi
  kill_port 8188 ComfyUI
  kill_port 8899 WebUI
  sleep 1
fi

echo "============================================"
echo " Coexistence mode: reuse running parts, start missing"
echo "============================================"

if comfy_alive; then
  echo "  [Reuse] ComfyUI already running on 8188"
else
  echo "============================================"
  echo " Starting ComfyUI (127.0.0.1:8188)"
  echo "============================================"
  VRAM_FLAG=""
  [ "${COMFY_VRAM_MODE:-}" = "high" ] && VRAM_FLAG="--highvram"
  rotate_log "$LOG_DIR/comfyui.log"
  (cd "$COMFY_ROOT" && nohup "$PY" -B ComfyUI/main.py $VRAM_FLAG --reserve-vram 1.5 \
      --force-channels-last --preview-method auto --fast \
      >> "$LOG_DIR/comfyui.log" 2>&1 &)
  echo "  Waiting for ComfyUI ready..."
  tries=0
  until comfy_alive; do
    tries=$((tries+1))
    [ "$tries" -ge 180 ] && { echo "  [ERROR] ComfyUI not ready in 180s, see $LOG_DIR/comfyui.log"; exit 1; }
    sleep 1
  done
  echo "  ComfyUI ready."
fi

if web_alive; then
  echo "  [Reuse] Web UI already running on 8899"
else
  echo "============================================"
  echo " Web UI (127.0.0.1:8899)"
  echo "============================================"
  rotate_log "$LOG_DIR/webui.log"
  (cd "$PROJ" && nohup "$PY" -B -m brain --web >> "$LOG_DIR/webui.log" 2>&1 & echo $! > "$AGENT_HOME/webui.pid")
  echo "  Waiting for Web UI ready..."
  tries=0
  until web_alive; do
    tries=$((tries+1))
    [ "$tries" -ge 30 ] && { echo "  [ERROR] Web UI not ready in 30s, see $LOG_DIR/webui.log"; exit 1; }
    sleep 1
  done
  echo "  Web UI ready."
fi

echo
echo "============================================"
echo " All ready!"
echo "   ComfyUI   : http://127.0.0.1:8188"
echo "   Brain Web : http://127.0.0.1:8899"
echo "   Logs      : $LOG_DIR"
echo "============================================"
if [ "$(uname)" = "Darwin" ]; then open "http://127.0.0.1:8899"
elif command -v xdg-open >/dev/null 2>&1; then xdg-open "http://127.0.0.1:8899"
fi
