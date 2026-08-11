@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch_kcd2_capture_studio.ps1"
if errorlevel 1 pause
