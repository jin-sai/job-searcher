@echo off
cd /d "%~dp0"

:menu
echo.
echo  Resume Builder
echo  ==============
echo  1. Build from job URL
echo  2. Build from pasted JD text
echo  3. Build for all sheet jobs without a resume
echo  4. Quit
echo.
set MODE=
set /p MODE="Select mode (1/2/3/4): "

if "%MODE%"=="1" goto mode1
if "%MODE%"=="2" goto mode2
if "%MODE%"=="3" goto mode3
if "%MODE%"=="4" goto end
echo Invalid choice. Please enter 1, 2, 3, or 4.
goto menu

:: ─── Mode 1: URL ──────────────────────────────────────────────────────────────
:mode1
echo.
set URL=
set /p URL="Job URL (leave blank to go back): "
if "%URL%"=="" goto menu

set TITLE=
set COMPANY=
set /p TITLE="Job title   (press Enter to skip): "
set /p COMPANY="Company name (press Enter to skip): "

set ARGS="%URL%"
if not "%TITLE%"==""   set ARGS=%ARGS% --title "%TITLE%"
if not "%COMPANY%"=="" set ARGS=%ARGS% --company "%COMPANY%"

echo.
python scraper/run_url.py %ARGS%
goto mode1

:: ─── Mode 2: JD text ──────────────────────────────────────────────────────────
:mode2
echo.
set TITLE=
set COMPANY=
set /p COMPANY="Company name (leave blank to go back): "
if "%COMPANY%"=="" goto menu
set /p TITLE="Job title: "

set ARGS=
if not "%TITLE%"==""   set ARGS=%ARGS% --title "%TITLE%"
if not "%COMPANY%"=="" set ARGS=%ARGS% --company "%COMPANY%"

echo.
python scraper/run_jd.py %ARGS%
goto mode2

:: ─── Mode 3: Sheet ────────────────────────────────────────────────────────────
:mode3
echo.
python scraper/run_sheet_resume.py
goto menu

:end
echo Goodbye.
pause
exit
