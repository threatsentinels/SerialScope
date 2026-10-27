# Usage: .\build.ps1            (tests + windowed exe)   .\build.ps1 -Console   (console exe for debugging)
param([switch]$Console, [switch]$SkipTests)
$ErrorActionPreference = "Stop"
if (-not $SkipTests) { python -m unittest discover -s tests; if ($LASTEXITCODE -ne 0) { throw "Tests failed" } }
if ($Console) { $env:SERIALSCOPE_CONSOLE = "1" } else { Remove-Item Env:SERIALSCOPE_CONSOLE -ErrorAction SilentlyContinue }
python -m PyInstaller --noconfirm --clean serialscope.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
Write-Host "Built: dist\" -ForegroundColor Green
