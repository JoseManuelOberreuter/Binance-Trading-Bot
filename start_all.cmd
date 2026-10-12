@echo off
rem Double-click to start the bots and the dashboard (see start_all.ps1).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_all.ps1" %*
