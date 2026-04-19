@echo off
REM Kill any process hanging onto port 8000 (the old crashed server)
for /f "tokens=5" %%a in ('netstat -aon ^| find ":8000" ^| find "LISTENING"') do taskkill /f /pid %%a >nul 2>&1

REM Start the server
python ocr_server.py
