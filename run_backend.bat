@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
echo Backend and frontend are served by the Docker app container.
echo Open http://127.0.0.1:8000/ after start_app.bat.
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%scripts\docker_app.ps1" logs
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo Command failed. Window will close in 5 seconds.
  timeout /t 5 /nobreak >nul
)
exit /b %EXIT_CODE%
