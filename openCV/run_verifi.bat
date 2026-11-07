@echo off
REM VeriFi full session: calibration on your own details + mock-crime test, every channel. See README.md.
REM   .venv\Scripts\python.exe examples\make_case.py p01          first: case file + sealed envelope
REM   run_verifi.bat --case data\cases\p01.json --sensor COM5      (~25-30 min; --quick ~15 min)
REM More options: run_verifi.bat --help
cd /d "%~dp0"
call "%~dp0setup.bat" || (pause & exit /b 1)
set GLOG_minloglevel=2
".venv\Scripts\python.exe" examples\verifi_session.py %*
pause
