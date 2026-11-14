@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul && (py -3 install.py & goto :eof)
where python >nul 2>nul && (python install.py & goto :eof)
echo Python n'est pas installe. Telechargez-le sur https://www.python.org/downloads/
echo (cochez "Add python.exe to PATH" pendant l'installation), puis relancez ce fichier.
pause
