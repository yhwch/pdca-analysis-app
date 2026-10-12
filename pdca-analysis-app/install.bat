@echo off
echo ============================================
echo  PDCA Analysis App - First-time setup
echo ============================================
echo.
echo This runs once. It will:
echo   1. Check that Python is installed
echo   2. Create a private Python environment for this app
echo   3. Download and install the required packages
echo   4. Set up the local database
echo.

python --version >NUL 2>&1
if errorlevel 1 (
    echo ERROR: Python not found.
    echo.
    echo Please install Python 3.10 or newer from:
    echo   https://www.python.org/downloads/
    echo.
    echo IMPORTANT: During installation, check the box
    echo "Add Python to PATH" before clicking Install.
    echo.
    pause
    exit /b 1
)

echo Step 1/3 - Creating a private Python environment for this app...
echo (This keeps the app's packages separate from anything else on your computer)
python -m venv venv
echo Done.
echo.

echo Step 2/3 - Installing required packages...
echo (Django, pandas, plotly, and a few others - may take 1-2 minutes)
call venv\Scripts\activate.bat
pip install -r requirements.txt
echo Done.
echo.

echo Step 3/3 - Setting up the local database...
echo (Creates the file that stores your saved experiments)
python manage.py migrate
echo Done.
echo.

echo ============================================
echo  Setup complete!
echo  Double-click start.bat to launch the app.
echo ============================================
echo.
pause
