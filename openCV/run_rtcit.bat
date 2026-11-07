@echo off
REM Card reaction-time test (~6 min): a quick demo with a secretly chosen playing card. See README.md.
REM Options: --no-camera  --no-check  --out data\name.trials.csv
cd /d "%~dp0"
call "%~dp0setup.bat" || (pause & exit /b 1)
set GLOG_minloglevel=2
".venv\Scripts\python.exe" examples\rt_cit_test.py %*
pause
