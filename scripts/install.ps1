# Native Windows does not support the V1 observation/hooks runtime. This wrapper
# delegates to WSL so the same Linux/macOS checks and paths are used.
param(
  [string]$Home,
  [string]$Workspace,
  [int]$Port,
  [switch]$WithHooks,
  [switch]$RemoveHooks,
  [switch]$WithOpenViking,
  [switch]$WithConsole,
  [switch]$Start,
  [switch]$Stop,
  [switch]$Status,
  [switch]$InstallService,
  [switch]$RemoveService,
  [switch]$OpenConsole,
  [switch]$DryRun
)
$root = Split-Path -Parent $PSScriptRoot
$setupArgs = @('--root', $root)
if ($Home) { $setupArgs += @('--home', $Home) }
if ($Port) { $setupArgs += @('--port', $Port) }
if ($WithHooks) { $setupArgs += '--with-hooks' }
if ($RemoveHooks) { $setupArgs += '--remove-hooks' }
if ($Workspace) { $setupArgs += @('--workspace', $Workspace) }
if ($WithOpenViking) { $setupArgs += '--with-openviking' }
if ($WithConsole) { $setupArgs += '--with-console' }
if ($Start) { $setupArgs += '--start' }
if ($Stop) { $setupArgs += '--stop' }
if ($Status) { $setupArgs += '--status' }
if ($InstallService) { $setupArgs += '--install-service' }
if ($RemoveService) { $setupArgs += '--remove-service' }
if ($OpenConsole) { $setupArgs += '--open-console' }
if ($DryRun) { $setupArgs += '--dry-run' }
if (Get-Command wsl.exe -ErrorAction SilentlyContinue) {
  $wslRoot = (& wsl.exe wslpath -a $root).Trim()
  $passthrough = $setupArgs | Select-Object -Skip 2
  $passthrough = $passthrough -replace [regex]::Escape($root), $wslRoot
  & wsl.exe --cd $wslRoot python3 scripts/sagacontext_setup.py @passthrough
} else {
  Write-Error 'SagaContext V1 requires WSL on Windows; install WSL and rerun this wrapper.'
  exit 1
}
exit $LASTEXITCODE
