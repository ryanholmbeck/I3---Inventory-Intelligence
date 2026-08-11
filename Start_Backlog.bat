@echo off
setlocal
title Backlog Review - Launcher
color 0B
:: Run from this file's own folder, wherever it lives (handles OneDrive paths).
cd /d "%~dp0"

echo.
echo  ==========================================
echo   BACKLOG REVIEW
echo  ==========================================
echo.

:: Already running? Just open it.
call :isup
if "%UP%"=="1" (
  echo  Server already running - opening app...
  goto open
)

echo  Starting the local server...
where python >nul 2>&1
if errorlevel 1 (
  echo.
  echo  Python was not found on PATH.
  echo  Install Python, or start "python server.py" yourself, then open
  echo  http://localhost:8765/Backlog_Review.html
  echo.
  pause
  exit /b
)

:: Launch the server in its own minimized window (stays open = the server).
start "Indelco Server" /min python server.py

:: Wait up to ~8s for it to bind the port.
set /a tries=0
:wait
call :isup
if "%UP%"=="1" goto open
set /a tries+=1
if %tries% geq 8 goto failed
timeout /t 1 /nobreak >nul
goto wait

:open
start "" "http://localhost:8765/Backlog_Review.html"
exit /b

:failed
echo.
echo  The server did not come up. Try running this from the same folder:
echo      python server.py
echo  then open  http://localhost:8765/Backlog_Review.html
echo.
pause
exit /b

:: ── is something listening on 8765? sets UP=1/0 ──
:isup
set "UP=0"
for /f %%S in ('powershell -NoProfile -Command "try{$c=New-Object Net.Sockets.TcpClient;$c.Connect('localhost',8765);$c.Close();'1'}catch{'0'}"') do set "UP=%%S"
goto :eof
