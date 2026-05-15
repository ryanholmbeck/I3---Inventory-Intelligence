@echo off
title Indelco — Daily Refresh
color 0A
echo.
echo  ==========================================
echo   INDELCO — Daily Data Refresh
echo   Pulls Indelco Plastics data only
echo  ==========================================
echo.
echo  Estimated time: 5-10 minutes
echo  Make sure you are on VPN first.
echo.

:: Pull Indelco Plastics data only
echo  [1/3] Pulling Indelco Plastics data...
python Indelco_SSMS_Connector.py --pull items    --company "Indelco Plastics" --output "%~dp0SSMS_Exports"
python Indelco_SSMS_Connector.py --pull ile      --company "Indelco Plastics" --output "%~dp0SSMS_Exports"
python Indelco_SSMS_Connector.py --pull values   --company "Indelco Plastics" --output "%~dp0SSMS_Exports"
python Indelco_SSMS_Connector.py --pull po       --company "Indelco Plastics" --output "%~dp0SSMS_Exports"
python Indelco_SSMS_Connector.py --pull qoh      --company "Indelco Plastics" --output "%~dp0SSMS_Exports"
python Indelco_SSMS_Connector.py --pull locations --company "Indelco Plastics" --output "%~dp0SSMS_Exports"

:: Rebuild live DB and merge with historical
echo.
echo  [2/3] Rebuilding database...
python build_db.py --mode live
if errorlevel 1 (
    echo  DB build failed. Check errors above.
    pause
    exit /b
)

:: Restart server if running
echo.
echo  [3/3] Refreshing server...
taskkill /f /fi "WINDOWTITLE eq Indelco Server*" >nul 2>&1
timeout /t 1 /nobreak >nul
start "Indelco Server" /min python server.py
timeout /t 2 /nobreak >nul

echo.
echo  ==========================================
echo   Refresh complete!
echo   Open: http://localhost:8765/Indelco_v3_Clean.html
echo  ==========================================
echo.
pause
