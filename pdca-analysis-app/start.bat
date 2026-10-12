@echo off
call venv\Scripts\activate.bat
echo Starting PDCA Analysis App...
echo Close this window to stop the server.
echo.
start "" http://localhost:8000
python manage.py runserver --noreload 8000
