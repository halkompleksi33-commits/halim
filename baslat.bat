@echo off
title Halim
chcp 65001 >nul
cd /d "%~dp0"
python -c "import flask, openpyxl" 2>nul || python -m pip install -r requirements.txt
start "" /min cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:5055"
python app.py
pause
