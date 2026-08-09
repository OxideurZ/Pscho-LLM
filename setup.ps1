[CmdletBinding()]
param()
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Need-Command([string]$name, [string]$help) {
  if (-not (Get-Command $name -ErrorAction SilentlyContinue)) { throw "$name est requis. $help" }
}

Need-Command python "Installez Python 3.12+ puis relancez .\setup.ps1."
Need-Command node "Installez Node.js LTS puis relancez .\setup.ps1."
Need-Command pnpm "Activez corepack (corepack enable) puis relancez .\setup.ps1."

if (-not (Test-Path .venv\Scripts\python.exe)) { python -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Push-Location frontend
pnpm install --frozen-lockfile
pnpm build
Pop-Location

& .\.venv\Scripts\python.exe -c "from backend.app.config.settings import Settings; s=Settings(); [p.mkdir(parents=True, exist_ok=True) for p in (s.data_directory/'runtime', s.data_directory/'logs', s.data_directory/'backups', s.data_directory/'models')]; print('Data:', s.data_directory); print('Model:', s.model_path); print('llama-server:', s.llama_server_path); print('STT model:', s.whisper_model_path); print('whisper-cli:', s.whisper_cpp_path)"
& .\.venv\Scripts\python.exe -m scripts.migrate
Write-Host "Setup terminé. Placez/validez le modèle et llama-server configurés, puis lancez .\start.ps1"
