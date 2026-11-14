param([int]$Port = 18443, [switch]$Load)
$ErrorActionPreference = 'Stop'
if ($Port -lt 1 -or $Port -gt 65535) { throw 'El puerto debe estar entre 1 y 65535.' }
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$composeFile = Join-Path $PSScriptRoot 'compose.yaml'
$loadFile = Join-Path $PSScriptRoot 'compose.load.yaml'
$project = 'ragproxy' + [guid]::NewGuid().ToString('N').Substring(0, 12)
$composeArgs = @('compose', '-p', $project, '-f', $composeFile)
if ($Load) { $composeArgs += @('-f', $loadFile) }
$certDir = Join-Path $env:TEMP ('rag-proxy-cert-' + [guid]::NewGuid().ToString('N'))
$oldTlsDir = $env:RAG_TEST_TLS_DIR
$oldPort = $env:RAG_PROXY_TEST_PORT
New-Item -ItemType Directory -Path $certDir | Out-Null
$env:RAG_TEST_TLS_DIR = $certDir
$env:RAG_PROXY_TEST_PORT = $Port.ToString()
try {
    $openssl = Get-Command openssl -ErrorAction SilentlyContinue
    if (-not $openssl) {
        $gitOpenSsl = 'C:\Program Files\Git\mingw64\bin\openssl.exe'
        if (-not (Test-Path -LiteralPath $gitOpenSsl)) { throw 'Se requiere OpenSSL.' }
        $opensslPath = $gitOpenSsl
    } else { $opensslPath = $openssl.Source }
    & $opensslPath req -x509 -newkey rsa:2048 -nodes -keyout (Join-Path $certDir 'key.pem') -out (Join-Path $certDir 'cert.pem') -days 1 -subj '/CN=localhost' -addext 'subjectAltName=DNS:localhost' 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo generar el certificado temporal.' }
    docker @composeArgs up -d --build --wait
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo iniciar el entorno aislado.' }
    if ($Load) {
        & (Join-Path $root '.venv/Scripts/python.exe') (Join-Path $PSScriptRoot 'load_probe.py') --ca (Join-Path $certDir 'cert.pem') --port $Port --project $project
    } else {
        & (Join-Path $root '.venv/Scripts/python.exe') (Join-Path $PSScriptRoot 'smoke_tls.py') --ca (Join-Path $certDir 'cert.pem') --port $Port
    }
    if ($LASTEXITCODE -ne 0) { throw 'La prueba aislada falló.' }
} finally {
    # El nombre único y el archivo fijo limitan la eliminación a este laboratorio efímero.
    docker @composeArgs down --volumes
    $resolved = [IO.Path]::GetFullPath($certDir)
    $tempRoot = [IO.Path]::GetFullPath($env:TEMP).TrimEnd('\') + '\'
    if ($resolved.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -and [IO.Path]::GetFileName($resolved).StartsWith('rag-proxy-cert-')) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
    $env:RAG_TEST_TLS_DIR = $oldTlsDir
    $env:RAG_PROXY_TEST_PORT = $oldPort
}
