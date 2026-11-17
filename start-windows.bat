@echo off
title terminjetzt - appointment watcher
cd /d "%~dp0"
where py >nul 2>nul && (py -3 terminjetzt.py %* & goto end)
where python >nul 2>nul && (python terminjetzt.py %* & goto end)
echo Python 3 is not installed. Get it from https://www.python.org/downloads/ (tick "Add Python to PATH").
:end
pause
