param(
  [ValidateSet("start", "rebuild", "stop", "status", "logs")]
  [string]$Action = "status"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$script:ComposeExitCode = 1

function Get-WslPathInfo {
  param([string]$Path)

  $normalizedPath = $Path -replace '^Microsoft\.PowerShell\.Core\\FileSystem::', ''

  if ($normalizedPath -match '^\\\\wsl(?:\.localhost|\$)\\([^\\]+)\\(.+)$') {
    return [pscustomobject]@{
      Distro = $Matches[1]
      Path = "/" + ($Matches[2] -replace "\\", "/")
    }
  }

  if ($normalizedPath -match '^[A-Za-z]:\\home\\') {
    return [pscustomobject]@{
      Distro = $null
      Path = $normalizedPath.Substring(2).Replace("\", "/")
    }
  }

  return $null
}

function Get-ComposeArgs {
  param([string]$ActionName)

  switch ($ActionName) {
    "start" { return @("compose", "up", "-d") }
    "rebuild" { return @("compose", "up", "-d", "--build") }
    "stop" { return @("compose", "down") }
    "status" { return @("compose", "ps") }
    "logs" { return @("compose", "logs", "-f", "app") }
  }
}

function Wait-AppHealth {
  $healthUrl = "http://127.0.0.1:8000/api/health"
  for ($i = 0; $i -lt 30; $i++) {
    try {
      $response = Invoke-WebRequest -UseBasicParsing -Uri $healthUrl -TimeoutSec 2
      if ($response.StatusCode -eq 200) {
        Write-Host "Health check passed: $healthUrl"
        return
      }
    } catch {
      Start-Sleep -Seconds 1
    }
  }
  Write-Host "Container started, but health check did not respond yet: $healthUrl"
}

function Invoke-WindowsCompose {
  param([string[]]$ComposeArgs)

  if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "Docker command was not found. Install Docker Desktop or Docker Engine first."
    $script:ComposeExitCode = 127
    return
  }

  $pushed = $false
  try {
    Push-Location -LiteralPath $ProjectRoot
    $pushed = $true
    & docker @ComposeArgs
    $script:ComposeExitCode = $LASTEXITCODE
  } catch {
    Write-Host "Windows Docker Compose failed: $($_.Exception.Message)"
    $script:ComposeExitCode = 1
  } finally {
    if ($pushed) {
      Pop-Location
    }
  }
}

function Invoke-WslCompose {
  param(
    [pscustomobject]$WslInfo,
    [string[]]$ComposeArgs
  )

  if (-not $WslInfo -or -not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    $script:ComposeExitCode = 127
    return
  }

  $distroCandidates = @()
  if ($env:WSL_DISTRO_OVERRIDE) {
    $distroCandidates += $env:WSL_DISTRO_OVERRIDE
  }
  if ($WslInfo.Distro) {
    $distroCandidates += $WslInfo.Distro
  }
  $distroCandidates += $null

  $lastCode = 127
  foreach ($distro in ($distroCandidates | Select-Object -Unique)) {
    $wslArgs = @()
    if ($distro) {
      $wslArgs += @("-d", $distro)
    }
    $wslArgs += @("--cd", $WslInfo.Path, "--", "docker")
    $wslArgs += $ComposeArgs

    & wsl.exe @wslArgs
    $lastCode = $LASTEXITCODE
    if ($lastCode -eq 0) {
      $script:ComposeExitCode = 0
      return
    }
  }

  $script:ComposeExitCode = $lastCode
}

function Show-DockerHelp {
  Write-Host ""
  Write-Host "Docker Compose could not be run from WSL or Windows."
  Write-Host "Recommended fix on Windows with Docker Desktop:"
  Write-Host "1. Open Docker Desktop."
  Write-Host "2. Settings -> Resources -> WSL Integration."
  Write-Host "3. Enable integration for the distro that contains this repo."
  Write-Host "4. Restart this script."
  Write-Host ""
  Write-Host "Quick check:"
  Write-Host "  wsl.exe --cd $($wslInfo.Path) -- docker compose ps"
  Write-Host "  docker compose ps"
  Write-Host ""
  Write-Host "Alternative: install Docker Engine inside WSL only if this machine will run Linux-native services without Docker Desktop."
}

$composeArgs = Get-ComposeArgs $Action
$wslInfo = Get-WslPathInfo $ProjectRoot
$exitCode = 1

if ($wslInfo) {
  Write-Host "Running Docker Compose from WSL path $($wslInfo.Path)."
  Invoke-WslCompose -WslInfo $wslInfo -ComposeArgs $composeArgs
  $exitCode = $script:ComposeExitCode
  if ($exitCode -ne 0) {
    Write-Host ""
    Write-Host "WSL Docker Compose failed. Trying Windows Docker CLI from the same project path..."
    Invoke-WindowsCompose -ComposeArgs $composeArgs
    $exitCode = $script:ComposeExitCode
    if ($exitCode -ne 0) {
      Show-DockerHelp
    }
  }
} else {
  Invoke-WindowsCompose -ComposeArgs $composeArgs
  $exitCode = $script:ComposeExitCode
}

if ($exitCode -eq 0 -and ($Action -eq "start" -or $Action -eq "rebuild")) {
  Write-Host ""
  Wait-AppHealth
  Write-Host "Started. Open http://127.0.0.1:8000/"
  if ($Action -eq "start") {
    Write-Host "Daily start reuses the existing image. After code updates, run rebuild_app.bat."
  }
}

exit $exitCode
