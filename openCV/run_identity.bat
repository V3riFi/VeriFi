@echo off
REM VeriFi concealed-identity test (~10 min; --quick ~7 min). See README.md.
REM   run_identity.bat                      your real details     (should be detected)
REM   run_identity.bat --control            a random identity     (should NOT be detected)
REM   run_identity.bat --sensor COM5        + the sensor round (Arduino; or udp:0.0.0.0:5008, or sim)
REM More options: run_identity.bat --help
cd /d "%~dp0"
call "%~dp0setup.bat" || (pause & exit /b 1)
set GLOG_minloglevel=2
".venv\Scripts\python.exe" examples\identity_test.py %*
pause
