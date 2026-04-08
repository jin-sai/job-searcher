@echo off
cd /d "%~dp0"

:loop
echo.
set URL=
set /p URL="Job URL (leave blank to quit): "
if "%URL%"=="" goto end

set TITLE=
set COMPANY=
set /p TITLE="Job Title (press Enter to skip): "
set /p COMPANY="Company (press Enter to skip): "

set ARGS="%URL%"
if not "%TITLE%"=="" set ARGS=%ARGS% --title "%TITLE%"
if not "%COMPANY%"=="" set ARGS=%ARGS% --company "%COMPANY%"

echo.
python scraper/run_url.py %ARGS%

goto loop

:end
echo Goodbye.
pause
