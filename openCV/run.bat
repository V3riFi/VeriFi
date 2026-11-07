@echo off
REM VeriFi live vision stream with on-screen HUD (for the backend / frontend). See VISION.md.
REM   run.bat                                        webcam + HUD
REM   run.bat --udp 127.0.0.1:5005 --control 127.0.0.1:5006   stream features, receive item onsets
REM Keys in the video window: q quit, b recalibrate baseline, m toggle mesh.
cd /d "%~dp0"
call "%~dp0setup.bat" || (pause & exit /b 1)
set GLOG_minloglevel=2
".venv\Scripts\python.exe" -m vision.main %*
if errorlevel 1 pause
