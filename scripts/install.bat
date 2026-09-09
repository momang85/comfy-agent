@echo off
chcp 65001 >nul
setlocal
REM ============================================================
REM  ComfyUI Brain - First-time setup (run once)
REM  Asks for your ComfyUI installation path, saves comfy_root.local
REM ============================================================
set "PROJ=%~dp0.."

echo ============================================
echo  ComfyUI Brain setup
echo ============================================
echo  Enter the full path of your ComfyUI installation folder.
echo  (the folder that contains python\python.exe and ComfyUI\main.py)
echo  Examples:
echo    D:\ComfyUI_windows_portable
echo    D:\comfyui\ComfyUI-aki-v3
echo.
set /p PATH_IN="ComfyUI path: "

if "%PATH_IN%"=="" (
  echo  [ERROR] Empty path.
  pause & exit /b 1
)
if not exist "%PATH_IN%\python\python.exe" (
  echo  [ERROR] python\python.exe not found under that path.
  echo  Check the path and retry.
  pause & exit /b 1
)
if not exist "%PATH_IN%\ComfyUI\main.py" (
  echo  [ERROR] ComfyUI\main.py not found under that path.
  pause & exit /b 1
)

> "%PROJ%\comfy_root.local" echo %PATH_IN%
echo.
echo  Saved. You can now run OneClickLaunch.bat.
echo  (To change later: delete comfy_root.local and run this again,
echo   or set environment variable COMFY_ROOT.)
pause
