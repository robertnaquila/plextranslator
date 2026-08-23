@echo off
REM One-time setup: creates a "Korean OCR Subtitles" shortcut on your Desktop
REM that runs watch-korean-ocr.bat, so you never need to open this folder again.
REM Just double-click this file once. Safe to re-run (recreates the shortcut).
title Create Desktop Shortcut
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0create-desktop-shortcut.ps1"
echo.
pause
