@echo off
setlocal
title Backlog Refresh - Install Daily Schedule
color 0B
cd /d "%~dp0"
set "TASK=Indelco Backlog Refresh"
set "RUNTIME=06:00"

echo.
echo  ==========================================
echo   BACKLOG REFRESH - Daily Auto-Update
echo  ==========================================
echo.
echo  Sets Windows to run the BC-^>Supabase refresh automatically every
echo  weekday morning, so orders are current before anyone reviews them.
echo.
echo  IMPORTANT: run this on ONE always-on machine that can reach BC and
echo  stays logged in (it uses your Windows login to talk to BC). It does
echo  NOT need to run on every rep's PC - they just open the web app.
echo.
set /p RUNTIME="  Run each weekday at (24h HH:MM) [%RUNTIME%]: "

schtasks /create /tn "%TASK%" /tr "\"%~dp0Refresh_Task.bat\"" /sc WEEKLY /d MON,TUE,WED,THU,FRI /st %RUNTIME% /f
echo.
if errorlevel 1 (
  echo  Could not create the task. If it says access denied, right-click this
  echo  file and choose "Run as administrator", then try again.
) else (
  echo  Scheduled "%TASK%" for weekdays at %RUNTIME%.
  echo.
  echo  - It runs while you are logged in. To run even when logged off, open
  echo    Task Scheduler, find the task, and tick "Run whether user is logged
  echo    on or not" ^(it will ask for your Windows password, stored securely^).
  echo  - Output is logged to  backlog\refresh.log
  echo  - To remove:  schtasks /delete /tn "%TASK%" /f
)
echo.
pause
