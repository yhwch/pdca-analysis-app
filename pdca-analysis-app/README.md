# PDCA Analysis App

A Django web app for comparing injection-moulding production data before and after a process change, as part of a PDCA (Plan-Do-Check-Act) improvement cycle. Engineers upload a machine export, pick two periods or two tools, and the app shows whether cycle times and quality got better or worse.

## Features

- Upload CSV or Excel (.xlsx/.xlsm) exports, with sheet selection for multi-sheet workbooks
- Compare two time periods, or two tools over the same period
- Data cleaning with selectable outlier methods and per-metric custom thresholds, with a count of removed cycles
- Metrics: average cycle times, absolute and percentage change, and a quality rate for feeding failures
- Automatic shift detection with change-point analysis (`ruptures`)
- Interactive Plotly charts (bar, scatter, distributions)
- Save comparisons as experiments, add notes, and revisit them later
- Print-friendly report and PDF export with server-rendered charts

## Tech

Python, Django, pandas, NumPy, SciPy, Plotly, ruptures, SQLite

## Run it

```bash
python -m venv venv
venv\Scripts\activate        # on macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

Then open http://localhost:8000. On Windows you can also double-click `install.bat` once and then `start.bat`. See [USER_GUIDE.txt](USER_GUIDE.txt) for a step-by-step guide written for non-technical users.

Set the `DJANGO_SECRET_KEY` environment variable before running it anywhere other than your own computer.

## Note

Production data is not included in this repository.
