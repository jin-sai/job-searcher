@echo off
cd /d "%~dp0"

echo.
echo  Add Job URLs
echo  ============
echo  Paste a job URL to scrape, filter, and add to the sheet.
echo  Type Q or leave blank to quit.
echo.

:loop
set URL=
set /p URL="Job URL: "

if /i "%URL%"=="Q" goto end
if "%URL%"=="" goto end

echo.
python scraper/add_url.py "%URL%"
echo.
goto loop

:end
echo.
echo Goodbye.
timeout /t 2 /nobreak >nul
exit
