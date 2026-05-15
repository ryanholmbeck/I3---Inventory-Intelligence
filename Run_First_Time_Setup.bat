@echo off
title Indelco — First Time Setup
color 0A
echo.
echo  ==========================================
echo   INDELCO — First Time Setup
echo   Run this ONCE to build historical data
echo  ==========================================
echo.
echo  This will:
echo  1. Pull INDELCO + AYER + CORR + QS data from SSMS
echo  2. Build indelco_historical.db (AYER/CORR/QS)
echo  3. Build indelco_live.db (Indelco Plastics)
echo  4. Merge into indelco.db
echo  5. Start the app server
echo.
echo  Estimated time: 15-25 minutes
echo  Make sure you are on VPN first.
echo.
pause

:: Step 1 - Pull all data
echo.
echo  [1/4] Pulling all data from SSMS...
call Run_Indelco_Pull.bat
if errorlevel 1 goto :error

:: Step 2 - Build full DB
echo.
echo  [2/4] Building databases...
python build_db.py --mode full
if errorlevel 1 goto :error

:: Step 3 - Start server
echo.
echo  [3/4] Starting app server...
start "Indelco Server" /min python server.py

:: Step 4 - Open browser
echo.
echo  [4/4] Opening app in browser...
timeout /t 2 /nobreak >nul
start http://localhost:8765/Indelco_v3_Clean.html

echo.
echo  ==========================================
echo   Setup complete!
echo   App: http://localhost:8765/Indelco_v3_Clean.html
echo   The server window is minimized in the taskbar.
echo   Keep it running while using the app.
echo  ==========================================
echo.
goto :end

:error
echo.
echo  Something went wrong. Check errors above.
pause

:end
