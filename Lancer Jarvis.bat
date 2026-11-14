@echo off
chcp 65001 >nul
title Jarvis
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 run.py %*
  goto fin
)
where python >nul 2>nul
if %errorlevel%==0 (
  python run.py %*
  goto fin
)
echo.
echo Python n'est pas installe sur ce PC.
echo Installez-le depuis https://www.python.org/downloads/ en cochant "Add python.exe to PATH",
echo puis relancez ce fichier.
echo.
pause
exit /b 1

:fin
if errorlevel 1 pause
