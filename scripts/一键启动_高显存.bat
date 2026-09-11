@echo off
REM ============================================================
REM  ComfyUI Brain - High-VRAM mode launcher
REM
REM  Pins models in GPU memory (--highvram) => faster for IMAGE-only
REM  workloads, BUT it disables ComfyUI's dynamic VRAM:
REM  large video models (e.g. MiniMax H3 INT8 ~20GB) will CRASH the
REM  server on a 12GB card. Use the normal 一键启动.bat for video.
REM ============================================================
set "COMFY_VRAM_MODE=high"
call "%~dp0一键启动.bat" %*
