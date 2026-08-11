@echo off
:: Silent refresh entry point for Task Scheduler (no prompts, logs to a file).
:: Run manually with Run_Backlog_Refresh.bat; this one is for the schedule.
cd /d "%~dp0"
if not exist "backlog\refresh_config.local.json" (
  echo [%date% %time%] SKIP - missing backlog\refresh_config.local.json >> "backlog\refresh.log"
  exit /b 1
)
echo [%date% %time%] refresh start >> "backlog\refresh.log"
python backlog\refresh_backlog.py >> "backlog\refresh.log" 2>&1
echo [%date% %time%] refresh exit %errorlevel% >> "backlog\refresh.log"
exit /b %errorlevel%
