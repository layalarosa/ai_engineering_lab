$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
& (Join-Path $root '.venv/Scripts/python.exe') (Join-Path $PSScriptRoot 'run.py')
if ($LASTEXITCODE -ne 0) { throw 'Falló la verificación temporal de PostgreSQL.' }
