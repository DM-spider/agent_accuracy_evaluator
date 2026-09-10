param(
  [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "Installing packaging extras..."
& $Python -m pip install -r requirements.txt

$dist = Join-Path $PSScriptRoot "dist"
New-Item -ItemType Directory -Force -Path $dist | Out-Null

& $Python -m PyInstaller --noconfirm --clean --onedir --name agent-accuracy-evaluator `
  --add-data "evaluator/static;evaluator/static" `
  --add-data "config/settings.example.toml;config" `
  --add-data "config/contracts.json;config" `
  --add-data "config/contracts_v3.json;config" `
  --add-data "config/metric_aliases.json;config" `
  --add-data "config/organization_aliases.json;config" `
  --add-data "data/golden/golden_dataset.json;data/golden" `
  --add-data "data/golden/漏损问答黄金测评集.xlsx;data/golden" `
  --add-data "data/golden/golden_dataset_v1.json;data/golden" `
  --add-data "data/golden/漏损问答黄金测评集_v1.xlsx;data/golden" `
  --add-data "data/golden/agent_answers_perfect.json;data/golden" `
  --add-data "data/golden/agent_answers_errors.json;data/golden" `
  --add-data "data/golden/agent_answers_mixed.json;data/golden" `
  --add-data "docs/模拟智能体20题.md;docs" `
  --add-data "data/golden/模拟智能体20题.md;data/golden" `
  --hidden-import uvicorn.logging --hidden-import uvicorn.protocols.http.auto `
  start.py

Copy-Item start_eval.bat (Join-Path $PSScriptRoot "dist/agent-accuracy-evaluator") -Force
Copy-Item config/settings.example.toml (Join-Path $PSScriptRoot "dist/agent-accuracy-evaluator") -Force

$sensitive = Get-ChildItem -Recurse (Join-Path $PSScriptRoot "dist/agent-accuracy-evaluator") -File | Where-Object {
  $_.Name -match "^\.env$|\.db\.env$|settings\.toml$" -or $_.Extension -in ".key", ".pem"
}
if ($sensitive) {
  throw "Refusing to package sensitive files: $($sensitive.FullName -join ', ')"
}

Write-Host "Package ready: dist/agent-accuracy-evaluator"
