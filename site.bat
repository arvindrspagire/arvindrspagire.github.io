@echo off
rem Double-click: build + open preview at http://localhost:8000  (uses miniconda myenv)
rem From a terminal:  site.bat build | site.bat new-alert | site.bat new-blog slug | site.bat serve --drafts
chcp 65001 >nul
cd /d "%~dp0"
set "PY=%USERPROFILE%\miniconda3\envs\myenv\python.exe"
if not exist "%PY%" (
  echo myenv Python not found: %PY%
  pause
  exit /b 1
)
if "%~1"=="" (
  start "" http://localhost:8000
  "%PY%" build.py serve
) else (
  "%PY%" build.py %*
)
if errorlevel 1 pause
