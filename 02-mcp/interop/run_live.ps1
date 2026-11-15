<# Prueba local aislada: contenedor temporal, sin reutilizar volúmenes existentes. #>
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$ragRoot = Join-Path (Split-Path -Parent $root) '01-rag'
$sdkPython = Join-Path $root '.venv/Scripts/python.exe'
$ragPython = Join-Path $ragRoot '.venv/Scripts/python.exe'
if (-not (Test-Path $sdkPython) -or -not (Test-Path $ragPython)) {
    throw 'Prepara los entornos virtuales de 01-rag y 02-mcp según sus README.'
}
$name = 'mcp-live-' + [guid]::NewGuid().ToString('N')
$env:POSTGRES_PASSWORD = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
$created = $false
try {
    $containerId = docker run --detach --rm --name $name --tmpfs /var/lib/postgresql/data --env POSTGRES_PASSWORD --env POSTGRES_USER=rag --env POSTGRES_DB=rag --publish 127.0.0.1::5432 pgvector/pgvector:0.8.0-pg16
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo iniciar PostgreSQL temporal.' }
    $created = $true
    $ready = $false
    for ($i = 0; $i -lt 60; $i++) {
        docker exec $name pg_isready -U rag -d rag *> $null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $ready) { throw 'PostgreSQL temporal no estuvo disponible.' }
    $mapping = docker port $name 5432/tcp
    if ($LASTEXITCODE -ne 0 -or $mapping -notmatch '^127\.0\.0\.1:(\d+)$') { throw 'No se pudo obtener el puerto local de PostgreSQL.' }
    $env:RAG_TEST_DATABASE_URL = 'postgresql://rag:' + $env:POSTGRES_PASSWORD + '@127.0.0.1:' + $Matches[1] + '/rag'
    $env:RAG_PYTHON = $ragPython
    $env:MCP_LIVE_TEST = '1'
    & $sdkPython -m unittest discover -s (Join-Path $root 'interop') -p 'test_live_rag.py' -v
    if ($LASTEXITCODE -ne 0) { throw 'La prueba real MCP-RAG falló.' }
} finally {
    if ($created) {
        docker stop $name *> $null
        if ($LASTEXITCODE -ne 0) { Write-Warning "No se pudo detener el contenedor temporal $name." }
    }
    Remove-Item Env:POSTGRES_PASSWORD, Env:RAG_TEST_DATABASE_URL, Env:RAG_PYTHON, Env:MCP_LIVE_TEST -ErrorAction SilentlyContinue
}
