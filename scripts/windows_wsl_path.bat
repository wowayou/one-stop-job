@echo off
setlocal
set "INPUT_PATH=%~1"

if "%INPUT_PATH%"=="" exit /b 1

for /f "tokens=1,2,* delims=\" %%A in ("%INPUT_PATH%") do (
  if /i "%%A"=="wsl.localhost" (
    endlocal & set "WSL_DETECTED=1" & set "WSL_DISTRO=%%B" & set "WSL_PATH=/%%C"
    exit /b 0
  )
  if /i "%%A"=="wsl$" (
    endlocal & set "WSL_DETECTED=1" & set "WSL_DISTRO=%%B" & set "WSL_PATH=/%%C"
    exit /b 0
  )
)

if /i "%INPUT_PATH:~0,8%"=="Z:\home\" (
  set "WSL_PATH=%INPUT_PATH:~2%"
  set "WSL_PATH=%WSL_PATH:\=/%"
  endlocal & set "WSL_DETECTED=1" & set "WSL_DISTRO=" & set "WSL_PATH=%WSL_PATH%"
  exit /b 0
)

endlocal & set "WSL_DETECTED=" & set "WSL_DISTRO=" & set "WSL_PATH="
exit /b 1
