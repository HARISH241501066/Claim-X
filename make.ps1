# Windows shim for the Makefile targets: .\make.ps1 data|api|web|test|web-test|e2e|reset|coverage
param([Parameter(Mandatory = $true)][ValidateSet('data', 'api', 'web', 'test', 'web-test', 'e2e', 'reset', 'coverage')][string]$Target,
       [string]$Extra = '')

$py = Join-Path $PSScriptRoot 'backend\.venv\Scripts\python.exe'
Set-Location $PSScriptRoot

switch ($Target) {
    'data' { & $py -m backend.data.generator }
    'api'  { & $py -m uvicorn backend.api.main:app --reload --port 8000 }
    'web'  { npm --prefix frontend run dev }
    'web-test' {
        npm --prefix frontend run lint
        if ($LASTEXITCODE -eq 0) { npm --prefix frontend test }
    }
    'e2e'  { npm --prefix frontend run e2e }
    'reset' { & $py -m backend.demo_reset $Extra }
    'coverage' { & $py -m pytest --cov=backend --cov-report=term-missing }
    'test' {
        & $py -m pytest
        if ($LASTEXITCODE -eq 0) { & $py -m ruff check backend }
    }
}
