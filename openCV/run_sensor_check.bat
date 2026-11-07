@echo off
REM VeriFi sensor check (~45 s), before every session with hardware. See HARDWARE.md.
REM   run_sensor_check.bat               lists the serial ports and asks
REM   run_sensor_check.bat COM5          or COM5@9600, udp:0.0.0.0:5008, sim
REM   run_sensor_check.bat COM5 --columns t,gsr,hr     (order of plain numbers in each line)
cd /d "%~dp0"
call "%~dp0setup.bat" || (pause & exit /b 1)
".venv\Scripts\python.exe" examples\sensor_check.py %*
pause
