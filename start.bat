@echo off
chcp 65001 >nul 2>&1
setlocal
cd /d "%~dp0"
title YRadar
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" monitor.py --loop %*
) else (
  python monitor.py --loop %*
)
if errorlevel 1 pause
