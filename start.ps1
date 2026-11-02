param([ValidateSet('web','feishu','test')][string]$Mode='web')
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
$projectPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    throw '请先按 README.md 创建虚拟环境并安装依赖。'
}
if ($Mode -eq 'feishu') { & $projectPython -m app.feishu }
elseif ($Mode -eq 'test') { & $projectPython -m pytest -q }
else { & $projectPython -m uvicorn app.main:app --host 127.0.0.1 --port 8000 }
exit $LASTEXITCODE
