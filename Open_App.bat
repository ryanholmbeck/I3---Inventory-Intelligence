@echo off
:: Check if server is running
curl -s http://localhost:8765/status >nul 2>&1
if errorlevel 1 (
    echo Starting server...
    start "Indelco Server" /min python server.py
    timeout /t 2 /nobreak >nul
)
start http://localhost:8765/Indelco_v3_Clean.html
