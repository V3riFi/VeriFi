@echo off
REM 60-second camera check: look at the screen naturally, no talking. Reports whether blinks are counted right.
cd /d "%~dp0"
call "%~dp0setup.bat" || (pause & exit /b 1)
set GLOG_minloglevel=2
".venv\Scripts\python.exe" examples\blink_check.py %*
pause
