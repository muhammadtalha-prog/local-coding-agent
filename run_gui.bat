@echo off
title IA Agent - Web Dashboard
echo ========================================
echo   🎨 IA Agent - Web Dashboard
echo ========================================
echo.

REM Activate virtual environment
call .\venv\Scripts\activate.bat

REM Check if Ollama is running
echo 🔍 Checking Ollama status...
curl -s http://localhost:11434/api/tags > nul
if errorlevel 1 (
    echo ⚠️  Ollama is not running!
    echo Starting Ollama in background...
    start /B ollama serve
    timeout /t 3 /nobreak > nul
)

echo ✅ Environment ready!
echo.
echo 🌐 Starting Web Dashboard Server...
echo 📍 Opening browser at: http://localhost:8000
echo.

REM Open the dashboard page in default browser
start http://localhost:8000

REM Run the custom Python FastAPI server (gui_server.py)
python gui_server.py

echo.
echo Press any key to close...
pause > nul
