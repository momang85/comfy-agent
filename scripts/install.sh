#!/usr/bin/env bash
# ============================================================
#  ComfyUI Brain - first-time setup (run once)  ·  macOS / Linux
#  Asks for your ComfyUI installation path, saves comfy_root.local
#  (Windows users: run scripts\install.bat instead)
# ============================================================
set -euo pipefail
PROJ="$(cd "$(dirname "$0")/.." && pwd)"

echo "============================================"
echo " ComfyUI Brain setup"
echo "============================================"

DEFAULT_ROOT="${COMFY_ROOT:-}"
if [ -z "$DEFAULT_ROOT" ] && [ -f "$PROJ/comfy_root.local" ]; then
  DEFAULT_ROOT="$(head -n1 "$PROJ/comfy_root.local")"
fi

# 找到“能跑 main.py 的 python”：便携包 python/bin、venv、系统 python3 都可
find_python() {
  local root="$1"
  for cand in "$root/python/bin/python3" "$root/venv/bin/python" "$root/venv/bin/python3"; do
    [ -x "$cand" ] && { echo "$cand"; return 0; }
  done
  command -v python3 >/dev/null 2>&1 && { command -v python3; return 0; }
  return 1
}

ROOT="$DEFAULT_ROOT"
if [ -z "$ROOT" ]; then
  # 常见安装位置探测（找到即用；都不在再问）
  for cand in "$HOME/ComfyUI" "$HOME/comfyui" "/opt/ComfyUI" "$HOME/ComfyUI-aki-v3"; do
    if [ -f "$cand/ComfyUI/main.py" ]; then ROOT="$cand"; echo "  [probe] found ComfyUI: $cand"; break; fi
  done
fi
if [ -z "$ROOT" ]; then
  printf "Path to your ComfyUI installation (dir containing ComfyUI/main.py): "
  read -r ROOT
fi
ROOT="${ROOT%/}"   # 去尾斜杠

if [ ! -f "$ROOT/ComfyUI/main.py" ]; then
  echo "  [ERROR] $ROOT/ComfyUI/main.py not found."
  echo "  Expected layout: <root>/ComfyUI/main.py (official repo layout)."
  echo "  Re-run and enter the correct path, or set COMFY_ROOT."
  exit 1
fi
if ! find_python "$ROOT" >/dev/null; then
  echo "  [ERROR] no usable python found (looked in $ROOT/python, $ROOT/venv, PATH)."
  exit 1
fi

printf '%s\n' "$ROOT" > "$PROJ/comfy_root.local"
chmod +x "$PROJ/scripts/start.sh" 2>/dev/null || true
echo "  [OK] saved to comfy_root.local"
PY="$(find_python "$ROOT")"
echo "  [OK] python: $PY"
echo
echo "Next: ./scripts/start.sh   →  open http://127.0.0.1:8899"
