@echo off
chcp 65001 >nul 2>&1
title YRadar warmup
cd /d "%~dp0"
set PYTHONPATH=.
python warmup_tesla.py
if errorlevel 1 pause
