@echo off
REM DocuLens OCR Server Launcher
REM This script clears port 8000 and starts the OCR server

echo Starting DocuLens OCR Server...

REM Kill any process hanging onto port 8000 (the old crashed server)
for /f "tokens=5" %%a in ('netstat -aon ^| find ":8000" ^| find "LISTENING"') do (
    echo Killing process on port 8000 (PID: %%a)...
    taskkill /f /pid %%a >nul 2>&1
)

REM Start the server
echo Starting OCR server...
python ocr_server.py

pause
