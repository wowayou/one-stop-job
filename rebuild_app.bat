@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%scripts\docker_app.ps1" rebuild
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo Command failed. Window will close in 5 seconds.
  timeout /t 5 /nobreak >nul
)
exit /b %EXIT_CODE%
