$ErrorActionPreference = 'Stop'

$projectRoot = $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$frontendIndex = Join-Path $projectRoot 'frontend\dist\index.html'

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Virtual environment not found. Follow the installation steps in README.md.'
}
if (-not (Test-Path -LiteralPath $frontendIndex)) {
    throw 'Frontend build not found. Run npm ci and npm run build inside frontend/.'
}

Set-Location -LiteralPath $projectRoot
& $pythonPath -m uvicorn securedesk.api:app --app-dir (Join-Path $projectRoot 'src') --host 127.0.0.1 --port 8000
