@echo off
cd /d "%~dp0"
if exist "%SystemRoot%\System32\wscript.exe" (
  start "" "%SystemRoot%\System32\wscript.exe" "%~dp0HANGAR.vbs"
) else (
  "%~dp0runtime\python.exe" -s "%~dp0launch.py"
)
