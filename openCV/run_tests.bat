@echo off
REM VeriFi offline test suite (no camera, no hardware needed).
cd /d "%~dp0"
call "%~dp0setup.bat" || (pause & exit /b 1)
".venv\Scripts\python.exe" -m unittest discover -s tests -v
pause
