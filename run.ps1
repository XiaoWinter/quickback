$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = "1"

if (-not (Test-Path .venv)) {
    py -3 -m venv .venv
}

& .\.venv\Scripts\python.exe -m pip install -r requirements.txt

if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "已生成 .env。把 GEMINI_API_KEY 填进去后，重启一次就能识别和反馈。"
}

$bind = & .\.venv\Scripts\python.exe -c "from app.config import settings; print(settings.host); print(settings.port)"
$bindHost = ($bind | Select-Object -First 1).Trim()
$bindPort = ($bind | Select-Object -Last 1).Trim()
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --host $bindHost --port $bindPort
