@echo off
setlocal
cd /d "%~dp0"
echo Checking game unpacking toolchain...
if exist "runtime\python.exe" (
    "runtime\python.exe" -B tools\install_game_tools.py %*
) else (
    python -B tools\install_game_tools.py %*
)
echo.
pause
