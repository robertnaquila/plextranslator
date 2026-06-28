@echo off
REM Double-click launcher for Korean live captions (best model + LLM refinement).
REM Runs scripts\watch-korean.ps1 with the execution policy relaxed for this run only.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0watch-korean.ps1"
echo.
echo (window stays open so you can read any messages)
pause
