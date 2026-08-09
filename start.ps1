[CmdletBinding()]
param([switch]$NoBrowser)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
if (-not (Test-Path .venv\Scripts\python.exe)) { throw "Environnement absent. Lancez d’abord .\setup.ps1." }
$arguments = @("-m", "scripts.launcher", "start")
if ($NoBrowser) { $arguments += "--no-browser" }
& .\.venv\Scripts\python.exe @arguments
