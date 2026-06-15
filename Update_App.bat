@echo off
title Indelco I3 - Update
color 0B
echo.
echo  ==========================================
echo   INDELCO I3 - Update to Latest Version
echo  ==========================================
echo.
echo  Downloading the newest files from GitHub...
echo.

set "REPO=ryanholmbeck/I3---Inventory-Intelligence"
set "BRANCH=claude/resume-session-8Zpf0"
set "BASE=https://raw.githubusercontent.com/%REPO%/%BRANCH%"

:: Cache-buster so GitHub's CDN can't hand us a stale copy
set "T=%RANDOM%%RANDOM%"

:: Files we keep in sync. The HTML is the main one; the others change rarely.
call :get "Indelco_v3_Clean.html"
call :get "build_db.py"
call :get "server.py"
call :get "Indelco_SSMS_Connector.py"
call :get "bc_pull.py"

echo.
echo  ==========================================
echo   Update complete.
echo  ==========================================
echo.
echo  Restart the app to load the new version?
choice /c YN /m "  Restart now"
if errorlevel 2 goto :end

echo.
echo  Restarting server...
taskkill /f /fi "WINDOWTITLE eq Indelco Server*" >nul 2>&1
timeout /t 1 /nobreak >nul
start "Indelco Server" /min python server.py
timeout /t 2 /nobreak >nul
start "" "http://localhost:8765/Indelco_v3_Clean.html"
goto :end

:get
echo  - %~1
powershell -NoProfile -Command ^
  "$ErrorActionPreference='Stop';" ^
  "try {" ^
  "  $u='%BASE%/%~1?cb=%T%';" ^
  "  $tmp='%~1.tmp';" ^
  "  Invoke-WebRequest -Uri $u -OutFile $tmp -Headers @{'Cache-Control'='no-cache'};" ^
  "  if ((Get-Item $tmp).Length -lt 100) { throw 'downloaded file is too small' }" ^
  "  Move-Item -Force $tmp '%~1';" ^
  "} catch {" ^
  "  Write-Host ('    FAILED: ' + $_.Exception.Message) -ForegroundColor Red;" ^
  "  if (Test-Path $tmp) { Remove-Item $tmp -Force }" ^
  "}"
goto :eof

:end
echo.
pause
