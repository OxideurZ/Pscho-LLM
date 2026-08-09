[CmdletBinding()]
param()
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
& .\.venv\Scripts\python.exe -m pytest
& .\.venv\Scripts\python.exe -m ruff check backend benchmark scripts
& .\.venv\Scripts\python.exe -m ruff format --check backend benchmark scripts
Push-Location frontend
pnpm test
pnpm exec tsc --noEmit
pnpm exec vite build
Pop-Location
git diff --check
