@echo off
REM Double-click launcher for Korean screen-OCR subtitles (burned-in subs -> English).
REM Runs scripts\watch-korean-ocr.ps1 with the execution policy relaxed for this run only.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0watch-korean-ocr.ps1"
echo.
echo (window stays open so you can read any messages)
pause
