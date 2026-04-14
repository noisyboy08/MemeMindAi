@echo off
cd /d "%~dp0"
if not exist ".env" (
  echo Missing .env - copy .env.example to .env and set GEMINI_API_KEY
  pause
  exit /b 1
)
echo Starting MEME MIND AI at http://127.0.0.1:8000
echo Open that URL in your browser. Press Ctrl+C to stop.
"%~dp0venv\Scripts\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8000
pause
