@echo off
rem musicq launcher: run with the .venv Python in this directory
set "PYTHONPATH=%~dp0"
"%~dp0.venv\Scripts\python.exe" -m musicq %*
