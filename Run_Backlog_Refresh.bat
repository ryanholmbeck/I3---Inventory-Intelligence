@echo off
setlocal
title Backlog Review - Refresh from Business Central
color 0B
echo.
echo  ==========================================
echo   BACKLOG REVIEW - Refresh from BC
echo  ==========================================
echo.

if not exist "backlog\refresh_config.local.json" (
  echo  Missing:  backlog\refresh_config.local.json
  echo.
  echo  Copy backlog\refresh_config.example.json to that name and fill in
  echo  your BC OData + OAuth details ^(the client secret stays in that
  echo  local file only^). Then run this again.
  echo.
  pause
  exit /b
)

:: First arg "probe" = dump entity fields without writing anything.
if /I "%~1"=="probe" (
  echo  PROBE mode - reading BC field names, no writes...
  python backlog\refresh_backlog.py --probe
) else (
  echo  Pulling open orders from BC and pushing to Supabase...
  python backlog\refresh_backlog.py
)

echo.
echo  ==========================================
echo   Done.
echo  ==========================================
echo.
pause
