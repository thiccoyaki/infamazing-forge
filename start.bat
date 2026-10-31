@echo off
REM Opens infamazing-forge in your browser. Close this window to stop it.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Creating venv...
  python -m venv .venv
  ".venv\Scripts\python" -m pip install --upgrade pip -q
)
".venv\Scripts\python" -m pip install -q -r requirements.txt
if not exist ".env" (
  echo No .env yet - copy .env.example to .env and add your ELEVENLABS_API_KEY ^(VO won't work until then^).
)
REM stop an older infamazing-forge still holding the port, so this window runs the current code
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8778 " ^| findstr LISTENING') do taskkill /PID %%p /F >nul 2>&1
".venv\Scripts\python" -m app
pause
