param([string]$Video, [string]$Bundle, [switch]$Twitch, [int]$Port = 8765)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$recallPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $recallPython)) { throw 'Run .\Setup.ps1 first.' }
$recallArguments = @('-m', 'tubelive.cli', 'serve', '--local-ai', '--port', "$Port")
if ($Video) { $recallArguments += @('--video', $Video); if (-not $Bundle) { $recallArguments += '--replay' } }
if ($Bundle) { $recallArguments += @('--bundle', $Bundle) }
if ($Twitch) { $recallArguments += '--twitch' }
& $recallPython @recallArguments
if ($LASTEXITCODE -ne 0) { throw 'Recall could not start. See the message above.' }
