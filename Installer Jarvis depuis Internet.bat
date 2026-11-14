@echo off
chcp 65001 >nul
echo Installation de Jarvis depuis GitHub...
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/maxencetrouillaud-stack/jarvis/main/install.ps1 | iex"
echo.
pause
