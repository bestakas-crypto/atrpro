@echo off
chcp 65001 > nul
title ATRsite-pro Server
color 0b

rem Use the directory this bat file lives in as the project root (no absolute path needed)
set PROJ_DIR=%~dp0
set BACKEND_DIR=%PROJ_DIR%backend

rem Force UTF-8 output so Korean instrument names in logs do not raise encoding errors
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

rem Make the atrsite package importable for worker.py regardless of current directory
set PYTHONPATH=%BACKEND_DIR%

rem Conda activation path (miniconda default install location)
set CONDA_PATH=%USERPROFILE%\miniconda3\Scripts\activate.bat
set CONDA_ENV=strpro

set WEB_HOST=127.0.0.1
set WEB_PORT=8811

echo ======================================================
echo  ATRsite-pro Server Starting...
echo ======================================================

echo [1/4] Activating Conda Environment: %CONDA_ENV%...
call "%CONDA_PATH%" %CONDA_ENV%

echo [2/4] Moving to Project Directory...
cd /d "%PROJ_DIR%"

rem This worker window is a separate process. Closing this main window does NOT
rem stop it; close the "ATRsite-pro Worker" window on its own when you are done.
echo [3/4] Launching Background Quote Worker (5-min polling)...
start "ATRsite-pro Worker" cmd /k "call ""%CONDA_PATH%"" %CONDA_ENV% && set PYTHONUTF8=1 && set PYTHONIOENCODING=utf-8 && set PYTHONPATH=%BACKEND_DIR% && cd /d ""%PROJ_DIR%"" && python -m atrsite.worker"

echo [4/4] Launching Web Server on port %WEB_PORT%...
echo.
echo Dashboard: http://%WEB_HOST%:%WEB_PORT%
echo.

rem Wait a few seconds for uvicorn to finish starting before opening the browser
start "" cmd /c "timeout /t 3 /nobreak > nul && start "" http://%WEB_HOST%:%WEB_PORT%"

python -m uvicorn atrsite.main:app --app-dir backend --host %WEB_HOST% --port %WEB_PORT%
pause
