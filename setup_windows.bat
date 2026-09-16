@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Python launcher not found. Install Python 3.11 or 3.12 from python.org.
  pause
  exit /b 1
)
py -3.12 -m venv .venv 2>nul || py -3.11 -m venv .venv
if errorlevel 1 exit /b 1
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if not exist .env copy .env.example .env >nul
echo.
echo Setup complete. Put broker credentials in .env, then run run_windows.bat.
pause

