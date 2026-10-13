<#
  SKYGYM - pure-PowerShell fallback runner for the Makefile targets.
  Used only when GNU Make is unavailable (see make.cmd).  Each target mirrors
  the Makefile recipe exactly so behaviour does not diverge between the two.
#>
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Target)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $RepoRoot

$Py = Join-Path $RepoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $Py)) {
    Write-Host "venv not found at $Py - run: python -m venv .venv" -ForegroundColor Red
    exit 1
}

function Py-Module([string]$m, [string[]]$rest) {
    & $Py -m $m @rest
    if ($LASTEXITCODE -ne 0) { throw "'python -m $m' failed with exit code $LASTEXITCODE" }
}
function Py-File([string]$f, [string[]]$rest) {
    & $Py $f @rest
    if ($LASTEXITCODE -ne 0) { throw "'python $f' failed with exit code $LASTEXITCODE" }
}
function Npm([string[]]$rest) {
    Push-Location (Join-Path $RepoRoot 'frontend')
    try { & npm @rest; if ($LASTEXITCODE -ne 0) { throw "npm $($rest -join ' ') failed" } }
    finally { Pop-Location }
}

function Invoke-Lint {
    Py-Module 'ruff' @('check', 'backend')
    Py-Module 'ruff' @('format', '--check', 'backend')
    Npm @('run', 'lint')
}
function Invoke-Typecheck {
    Py-Module 'mypy' @('--config-file', 'pyproject.toml')
    Npm @('run', 'typecheck')
}
function Invoke-Test {
    Py-Module 'pytest' @('backend/tests', '-q', '-m', 'not slow')
}
function Invoke-TestFull { Py-Module 'pytest' @('backend/tests', '-q') }
function Invoke-Check {
    Invoke-Lint; Invoke-Typecheck; Invoke-Test
    Write-Host "`n==== make check: ALL GREEN ====" -ForegroundColor Green
}
function Invoke-Seed { Py-File 'backend/scripts/seed.py' @() }
function Invoke-Dev {
    Invoke-Seed
    $env:BACKEND_URL = 'http://127.0.0.1:8000'
    $backend = Start-Process -FilePath $Py -ArgumentList @(
        '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000',
        '--app-dir', 'backend', '--reload') -PassThru -NoNewWindow
    try {
        Npm @('run', 'dev', '--', '--host', '127.0.0.1', '--port', '5173')
    }
    finally {
        if ($backend -and -not $backend.HasExited) { Stop-Process -Id $backend.Id -Force }
    }
}
function Invoke-Demo {
    Invoke-Seed
    Write-Host "SKYGYM demo ready.  Now run: .\make.cmd dev" -ForegroundColor Green
    Invoke-Dev
}

if (-not $Target -or $Target.Count -eq 0) { $Target = @('help') }

foreach ($t in $Target) {
    Write-Host ">> make $t" -ForegroundColor Cyan
    switch ($t) {
        'help' { Write-Host 'targets: install dev check check-all lint format typecheck test test-full seed demo headless schemas clean' }
        'install' { Py-Module 'pip' @('install', '-r', 'backend/requirements.txt'); Npm @('install') }
        'install-frontend' { Npm @('install') }
        'install-ml' { Py-Module 'pip' @('install', '-r', 'backend/requirements-ml.txt') }
        'dev' { Invoke-Dev }
        'check' { Invoke-Check }
        'check-all' { Invoke-Lint; Invoke-Typecheck; Py-Module 'pytest' @('backend/tests', '-m', 'slow or perf', '-q') }
        'lint' { Invoke-Lint }
        'format' { Py-Module 'ruff' @('format', 'backend'); Py-Module 'ruff' @('check', '--fix', 'backend'); Npm @('run', 'format') }
        'typecheck' { Invoke-Typecheck }
        'test' { Invoke-Test }
        'test-full' { Invoke-TestFull }
        'seed' { Invoke-Seed }
        'seed-db' { Invoke-Seed }
        'reset-db' { Py-File 'backend/scripts/reset_db.py' @() }
        'demo' { Invoke-Demo }
        'headless' { Push-Location backend; try { & $Py -m 'app.core.headless' } finally { Pop-Location } }
        'schemas' { Push-Location backend; try { & $Py -m 'app.core.schema_export' } finally { Pop-Location } }
        'clean' { Write-Host 'clean: remove frontend/dist, caches and the sqlite db'; Remove-Item -Recurse -Force -ErrorAction SilentlyContinue 'frontend/dist', '.pytest_cache', '.ruff_cache', 'backend/data/skygym.db' }
        default { Write-Host "unknown target: $t" -ForegroundColor Red; exit 2 }
    }
}
exit 0