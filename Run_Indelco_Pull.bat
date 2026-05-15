@echo off
title Indelco SSMS Data Pull v5
color 0A
echo.
echo  ==========================================
echo   INDELCO INVENTORY - SSMS Data Pull v5
echo  ==========================================
echo.

python --version >nul 2>&1
if errorlevel 1 (echo Python not found & pause & exit /b)
python -c "import pyodbc" >nul 2>&1
if errorlevel 1 (pip install pyodbc --quiet)

echo  Testing connection...
echo.
python Indelco_SSMS_Connector.py --test
if errorlevel 1 (
    echo. & echo Connection failed - check VPN & pause & exit /b
)

echo.
echo  Pulling all data (10-20 minutes)...
echo.

echo  ── Indelco Plastics (main) ──────────────────
python Indelco_SSMS_Connector.py --pull items   --company "Indelco Plastics" --output "%~dp0SSMS_Exports"
python Indelco_SSMS_Connector.py --pull ile     --company "Indelco Plastics" --output "%~dp0SSMS_Exports"
python Indelco_SSMS_Connector.py --pull values  --company "Indelco Plastics" --output "%~dp0SSMS_Exports"
python Indelco_SSMS_Connector.py --pull po      --company "Indelco Plastics" --output "%~dp0SSMS_Exports"
python Indelco_SSMS_Connector.py --pull qoh     --company "Indelco Plastics" --output "%~dp0SSMS_Exports"
python Indelco_SSMS_Connector.py --pull locations --company "Indelco Plastics" --output "%~dp0SSMS_Exports"

echo.
echo  ── Ayer Sales (legacy) ──────────────────────
python Indelco_SSMS_Connector.py --pull items   --company "Ayer Sales"       --output "%~dp0SSMS_Exports"
python Indelco_SSMS_Connector.py --pull ile     --company "Ayer Sales"       --output "%~dp0SSMS_Exports"
python Indelco_SSMS_Connector.py --pull values  --company "Ayer Sales"       --output "%~dp0SSMS_Exports"

echo.
echo  ── Corr Tech (legacy) ───────────────────────
python Indelco_SSMS_Connector.py --pull items   --company "Corr Tech"        --output "%~dp0SSMS_Exports"
python Indelco_SSMS_Connector.py --pull ile     --company "Corr Tech"        --output "%~dp0SSMS_Exports"
python Indelco_SSMS_Connector.py --pull values  --company "Corr Tech"        --output "%~dp0SSMS_Exports"

echo.
echo  ── Quality Stainless (legacy) ───────────────
python Indelco_SSMS_Connector.py --pull items   --company "Quality Stainless" --output "%~dp0SSMS_Exports"
python Indelco_SSMS_Connector.py --pull ile     --company "Quality Stainless" --output "%~dp0SSMS_Exports"
python Indelco_SSMS_Connector.py --pull values  --company "Quality Stainless" --output "%~dp0SSMS_Exports"

echo.
echo  ==========================================
echo   Done! Files saved to SSMS_Exports\
echo  ==========================================
echo.
echo  Files to import into Indelco_v3_Clean.html:
echo.
echo  ITEM MASTER zone (drop one at a time):
echo    Items_INDELCO_*.csv
echo    Items_AYER_*.csv
echo    Items_CORR_*.csv
echo    Items_QS_*.csv
echo.
echo  ILE DATA zone (drop one at a time):
echo    ILE_INDELCO_*.csv
echo    ILE_AYER_*.csv
echo    ILE_CORR_*.csv
echo    ILE_QS_*.csv
echo.
echo  VALUE ENTRIES zone:
echo    ValueEntry_INDELCO_*.csv
echo.
echo  PURCHASE ORDERS zone:
echo    PO_INDELCO_*.csv
echo.
echo  QoH / LOCATIONS zone:
echo    QoH_INDELCO_*.csv
echo    Locations_INDELCO_*.csv
echo.
pause
