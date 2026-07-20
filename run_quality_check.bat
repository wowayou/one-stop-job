@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "WSL_RUNNER=wsl.exe"

for /f "tokens=1,2,* delims=\" %%A in ("%SCRIPT_DIR%") do (
  if /i "%%A"=="wsl.localhost" (
    set "WSL_PATH=/%%C"
    goto :run_wsl
  )
  if /i "%%A"=="wsl$" (
    set "WSL_PATH=/%%C"
    goto :run_wsl
  )
)

echo This quality check script is intended for the WSL project path.
echo Window will close in 5 seconds.
timeout /t 5 /nobreak >nul
exit /b 1

:run_wsl
set "WSL_PATH=%WSL_PATH:\=/%"
if not "%WSL_DISTRO_OVERRIDE%"=="" set "WSL_RUNNER=wsl.exe -d %WSL_DISTRO_OVERRIDE%"
set "PATH=%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\WindowsPowerShell\v1.0"
%WSL_RUNNER% -- bash -lc "cd '%WSL_PATH%' && chmod +x scripts/quality_gate.sh scripts/system_smoke.sh && scripts/quality_gate.sh"
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo Command failed. Window will close in 5 seconds.
  timeout /t 5 /nobreak >nul
)
exit /b %EXIT_CODE%
