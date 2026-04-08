@echo off
cd /d "%~dp0"

echo.
echo == Step 1: Scraping career pages ==
echo.
python run.py
if errorlevel 1 (
    echo.
    echo [ERROR] Scraper failed.
    pause
    exit /b 1
)

echo.
echo == Step 2: Filtering jobs ==
echo.
python scraper/filter_jobs.py

echo.
pause
