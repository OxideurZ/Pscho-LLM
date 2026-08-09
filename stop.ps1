[CmdletBinding()]
param()
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
if (-not (Test-Path .venv\Scripts\python.exe)) { throw "Environnement absent. Lancez d’abord .\setup.ps1." }
& .\.venv\Scripts\python.exe -m scripts.launcher stop
