@echo off
cd /d "%~dp0"
echo Checking packages. Internet is needed only the first time.
if not exist requirements.txt (
  echo ERROR: requirements.txt not found in this folder.
  echo The file name must be exactly requirements.txt
  pause
  exit /b 1
)
if not exist app.py (
  echo ERROR: app.py not found in this folder.
  pause
  exit /b 1
)
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo ERROR: package installation failed. See the message above.
  pause
  exit /b 1
)
echo Starting local-only app at 127.0.0.1
python -m streamlit run app.py --server.address 127.0.0.1 --browser.gatherUsageStats false
pause
