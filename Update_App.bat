@echo off
setlocal
title Indelco I3 - Update
color 0B
set "SELF=%~f0"
echo.
echo  ==========================================
echo   INDELCO I3 - Update to Latest Version
echo  ==========================================
echo.
echo  Downloading the newest files from GitHub...
echo.

set "REPO=ryanholmbeck/I3---Inventory-Intelligence"
set "BRANCH=main"
set "BASE=https://raw.githubusercontent.com/%REPO%/%BRANCH%"

:: Cache-buster so GitHub's CDN can't hand us a stale copy
set "T=%RANDOM%%RANDOM%"

:: App files kept in sync. The HTML is the main one; the others change rarely.
call :get "Indelco_v3_Clean.html"
call :get "build_db.py"
call :get "server.py"
call :get "Indelco_SSMS_Connector.py"
call :get "bc_pull.py"

:: Self-update: download the latest updater to .new (can't overwrite a
:: running .bat in place; we swap it in after this script exits, below).
echo  - Update_App.bat (self)
powershell -NoProfile -Command ^
  "try {" ^
  "  Invoke-WebRequest -Uri '%BASE%/Update_App.bat?cb=%T%' -OutFile 'Update_App.bat.new' -Headers @{'Cache-Control'='no-cache'};" ^
  "  if ((Get-Item 'Update_App.bat.new').Length -lt 100) { Remove-Item 'Update_App.bat.new' -Force }" ^
  "} catch { if (Test-Path 'Update_App.bat.new') { Remove-Item 'Update_App.bat.new' -Force } }"

echo.
echo  ==========================================
echo   Update complete.
echo  ==========================================
echo.
echo  Restart the app to load the new version?
choice /c YN /m "  Restart now"
if errorlevel 2 goto :selfupdate

echo.
echo  Restarting server...
taskkill /f /fi "WINDOWTITLE eq Indelco Server*" >nul 2>&1
timeout /t 1 /nobreak >nul
start "Indelco Server" /min python server.py
timeout /t 2 /nobreak >nul
start "" "http://localhost:8765/Indelco_v3_Clean.html"

:selfupdate
:: If a newer updater was downloaded and it differs, swap it in AFTER this
:: script exits (a detached cmd waits 1s, then replaces the file).
if exist "Update_App.bat.new" (
  fc /b "Update_App.bat.new" "%SELF%" >nul 2>&1
  if errorlevel 1 (
    echo.
    echo  Updater itself was updated - applying on exit.
    start "" /min cmd /c "timeout /t 1 >nul & move /y ""Update_App.bat.new"" ""%SELF%"" >nul 2>&1"
  ) else (
    del "Update_App.bat.new" >nul 2>&1
  )
)

echo.
pause
