[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Create .venv with 64-bit Python 3.12, then install requirements-build.txt.'
}

& $python -X utf8 (Join-Path $PSScriptRoot 'build_windows.py')
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
