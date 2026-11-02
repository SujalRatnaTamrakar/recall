param([switch]$SkipSpeechDownload, [switch]$SkipSemanticDownload)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw 'Install uv first: https://docs.astral.sh/uv/getting-started/installation/' }
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { throw 'Install FFmpeg and add it to PATH before setup.' }
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) { throw 'Install Ollama and add the local qwen3.5:9b model before setup.' }
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    uv venv --python 3.12 .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create Python environment.' }
}
uv pip install --python '.venv\Scripts\python.exe' -e '.[local,test]'
if ($LASTEXITCODE -ne 0) { throw 'Could not install Python dependencies.' }
if (-not $SkipSemanticDownload) {
    & '.venv\Scripts\python.exe' -m tubelive.cli prepare-semantic
    if ($LASTEXITCODE -ne 0) { throw 'Could not prepare local meaning search. Retry, or use -SkipSemanticDownload for keyword search only.' }
}
Push-Location -LiteralPath (Join-Path $PSScriptRoot 'viewer')
try {
    npm.cmd ci --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'Could not install viewer dependencies.' }
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Could not build the viewer.' }
} finally { Pop-Location }
if (-not $SkipSpeechDownload) {
    & '.venv\Scripts\python.exe' -m tubelive.cli prepare
    if ($LASTEXITCODE -ne 0) { throw 'Finish the model setup shown above before starting.' }
}
Write-Host 'Setup complete. Run .\Start.ps1 to open Recall.'
