# Windows shim for the Makefile targets: .\make.ps1 data|api|web|test
param([Parameter(Mandatory = $true)][ValidateSet('data', 'api', 'web', 'test')][string]$Target)

$py = Join-Path $PSScriptRoot 'backend\.venv\Scripts\python.exe'
Set-Location $PSScriptRoot

switch ($Target) {
    'data' { Write-Host 'data module not built yet (later module)' }
    'api'  { & $py -m uvicorn backend.api.main:app --reload --port 8000 }
    'web'  { npm --prefix frontend run dev }
    'test' {
        & $py -m pytest
        if ($LASTEXITCODE -eq 0) { & $py -m ruff check backend }
    }
}
