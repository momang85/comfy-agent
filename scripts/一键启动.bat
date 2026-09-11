@echo off
setlocal enabledelayedexpansion

REM ============================================================
REM  ComfyUI Brain One-Click Launcher (Coexistence Mode)
REM
REM  Default (double-click):
REM    - ComfyUI running on 8188 (e.g. managed by hui-shi launcher)?
REM      -> REUSE it, never kill, never start a duplicate.
REM    - Web UI running on 8899? -> REUSE it.
REM    - Missing parts are started fresh (wait until ready).
REM    Never touches the hui-shi launcher process.
REM
REM  Full restart (fresh everything):
REM    一键启动.bat /restart
REM    -> stop all (8188/8899 listeners + hui-shi launcher),
REM       verify ports free, then start fresh.
REM ============================================================

REM Portable paths: project root = parent of this script's folder;
REM ComfyUI from env COMFY_ROOT -> comfy_root.local -> install.bat guide
set "PROJ=%~dp0.."
if "%COMFY_ROOT%"=="" if exist "%PROJ%\comfy_root.local" set /p COMFY_ROOT=<"%PROJ%\comfy_root.local"
if "%COMFY_ROOT%"=="" (
  echo   [ERROR] ComfyUI path not configured.
  echo   Run install.bat once to set it, or set env var COMFY_ROOT.
  pause & exit /b 1
)
set "PY=%COMFY_ROOT%\python\python.exe"
set "MAIN=%COMFY_ROOT%\ComfyUI\main.py"
set "AGENT_HOME=%PROJ%\.comfy-agent"
set "LOG_DIR=%AGENT_HOME%\logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

if /I "%~1"=="/restart" goto full_restart
goto coexist

REM ============================================================
REM  FULL RESTART MODE
REM ============================================================
:full_restart
echo ============================================
echo  Restart mode: stop everything, then start fresh
echo ============================================

if exist "%AGENT_HOME%\webui.pid" (
  set /p OLDPID=<"%AGENT_HOME%\webui.pid"
  tasklist /FI "PID eq !OLDPID!" 2>nul | findstr "!OLDPID!" >nul && (
    taskkill /F /PID !OLDPID! >nul 2>&1
    echo   [Web UI] stopped old pid=!OLDPID!
  )
  del "%AGENT_HOME%\webui.pid" >nul 2>&1
)
call :kill_port 8188 ComfyUI
call :kill_port 8899 WebUI
tasklist /FI "IMAGENAME eq StableDiffusionWebUILauncher.exe" 2>nul | findstr "StableDiffusionWebUILauncher.exe" >nul && (
  taskkill /F /IM StableDiffusionWebUILauncher.exe >nul 2>&1
  echo   [Launcher] stopped hui-shi launcher
)
call :port_free 8188 || (
  echo   [ERROR] port 8188 still in use.
  pause & exit /b 1
)
call :port_free 8899 || (
  echo   [ERROR] port 8899 still in use.
  pause & exit /b 1
)
echo   Ports released. Starting fresh...
goto start_comfy

REM ============================================================
REM  COEXISTENCE MODE (default)
REM ============================================================
:coexist
echo ============================================
echo  Coexistence mode: reuse running parts, start missing
echo ============================================

call :comfy_alive && (
  echo   [Reuse] ComfyUI already running on 8188
  goto web_check
)
echo   ComfyUI not running, will start fresh...

:start_comfy
echo ============================================
echo  Starting ComfyUI (127.0.0.1:8188)
echo ============================================
if not exist "%PY%" (
  echo   [ERROR] embedded python not found: %PY%
  pause & exit /b 1
)
REM VRAM mode: default = ComfyUI dynamic VRAM (safe for large video models on 12GB).
REM Set COMFY_VRAM_MODE=high to pin models in VRAM (image-only workloads, much faster
REM but CRASHES with big video models such as MiniMax H3 INT8 ~20GB).
set "VRAM_FLAG="
if /I "%COMFY_VRAM_MODE%"=="high" set "VRAM_FLAG=--highvram"
start "ComfyUI" /D "%COMFY_ROOT%" /MIN cmd /c ""%PY%" -B "%MAIN%" !VRAM_FLAG! --reserve-vram 1.5 --force-channels-last --preview-method auto --fast > "%LOG_DIR%\comfyui.log" 2>&1"
echo   Waiting for ComfyUI ready...
set /a TRIES=0
:wait_comfy
set /a TRIES+=1
call :comfy_alive && goto comfy_ready
if !TRIES! GEQ 180 (
  echo   [ERROR] ComfyUI not ready in 180s, see %LOG_DIR%\comfyui.log
  pause & exit /b 1
)
powershell -NoProfile -Command "Start-Sleep -Seconds 1"
goto wait_comfy
:comfy_ready
echo   ComfyUI ready.

:web_check
echo ============================================
echo  Web UI (127.0.0.1:8899)
echo ============================================
call :web_alive && (
  echo   [Reuse] Web UI already running on 8899
  goto all_ready
)
start "ComfyUI-Brain-WebUI" /D "%PROJ%" /MIN cmd /c ""%PY%" -B -m brain --web > "%LOG_DIR%\webui.log" 2>&1"
echo   Waiting for Web UI ready...
set /a TRIES=0
:wait_web
set /a TRIES+=1
call :web_alive && goto web_ready
if !TRIES! GEQ 30 (
  echo   [ERROR] Web UI not ready in 30s, see %LOG_DIR%\webui.log
  pause & exit /b 1
)
powershell -NoProfile -Command "Start-Sleep -Seconds 1"
goto wait_web
:web_ready
echo   Web UI ready.

:all_ready
echo.
echo ============================================
echo  All ready!
echo    ComfyUI   : http://127.0.0.1:8188
echo    Brain Web : http://127.0.0.1:8899
echo    Logs      : %LOG_DIR%
echo ============================================
start "" http://127.0.0.1:8899
powershell -NoProfile -Command "Start-Sleep -Seconds 3"
exit /b 0

REM ---------------- subroutines ----------------

:kill_port
REM %1=port %2=name : kill the process LISTENING on the port
set "PORT=%~1"
set "NAME=%~2"
set "FOUND="
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%PORT% " ^| findstr LISTENING') do set "FOUND=%%p"
if defined FOUND (
  taskkill /F /PID %FOUND% >nul 2>&1
  echo   [%NAME%] stopped old pid=%FOUND%
)
exit /b 0

:port_free
REM %1=port : returns 1 if occupied (listener exists), 0 if free
set "P=%~1"
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%P% " ^| findstr LISTENING') do exit /b 1
exit /b 0

:comfy_alive
REM returns 0 if a real ComfyUI answers on 8188
powershell -NoProfile -Command "try { $j = Invoke-RestMethod -Uri 'http://127.0.0.1:8188/system_stats' -TimeoutSec 3; if ($j.system.comfyui_version) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
exit /b %errorlevel%

:web_alive
REM returns 0 if our Web UI answers on 8899
powershell -NoProfile -Command "try { $j = Invoke-RestMethod -Uri 'http://127.0.0.1:8899/api/status' -TimeoutSec 3; if ($j.ok -ne $null) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
exit /b %errorlevel%
