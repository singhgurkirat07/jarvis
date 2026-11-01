# Launch the Jarvis local assistant server on 127.0.0.1:8000 using Python 3.12
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
Write-Host "Starting Jarvis on http://127.0.0.1:8000 ..." -ForegroundColor Cyan
py -3.12 -m backend @args
