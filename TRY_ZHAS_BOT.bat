@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
call venv\Scripts\activate.bat
echo Zhas Bot (базалық) — нөмірін немесе атауын жазып, Enter бас. Шығу: Ctrl+C
echo.
python -m scripts.chat_cli zhas-bot
pause
