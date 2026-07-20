@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "API_BASE=http://127.0.0.1:8000"
if not "%~1"=="" (
  echo %~1 | findstr /B /I "http:// https://" >nul
  if not errorlevel 1 (
    set "API_BASE=%~1"
    shift /1
  )
)

where py >nul 2>nul
if "%ERRORLEVEL%"=="0" (
  py -3 "%SCRIPT_DIR%host_opencli_import.py" --source zhilian --api "%API_BASE%" %*
) else (
  python "%SCRIPT_DIR%host_opencli_import.py" --source zhilian --api "%API_BASE%" %*
)
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo Command failed. Window will close in 5 seconds.
  timeout /t 5 /nobreak >nul
)
exit /b %EXIT_CODE%
