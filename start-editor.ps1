$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
$dockerCandidates=@(
 (Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin\docker.exe'),
 'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
)
$dockerCandidates+=@(Get-Command docker.exe -All -ErrorAction SilentlyContinue | ForEach-Object Source)
$dockerExe=$dockerCandidates | Where-Object { (Test-Path -LiteralPath $_ -PathType Leaf) -and (Get-Item -LiteralPath $_).Length -gt 0 } | Select-Object -First 1
if (-not $dockerExe) { throw '未找到可用的 Docker Desktop，请安装或检查安装路径。https://www.docker.com/products/docker-desktop/' }
Write-Host "使用 Docker：$dockerExe"
& $dockerExe info --format '{{.OSType}}'
if ($LASTEXITCODE -ne 0) { throw 'Docker Desktop 尚未启动，请打开后重试。' }
$projectPython=Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$editorSecret=& $projectPython -c 'from app.online_word import secret;from app.store import Store;from app.config import Settings;print(secret(Store(Settings().data_dir)))'
if ($LASTEXITCODE -ne 0) { throw '无法准备编辑服务密钥' }
$containerName='shiban-onlyoffice'
$existing=& $dockerExe ps -a --filter "name=^/$containerName$" --format '{{.Names}}'
if ($existing) { & $dockerExe start $containerName }
else {
  & $dockerExe run -d --name $containerName --restart unless-stopped -p '127.0.0.1:8082:80' -e 'JWT_ENABLED=true' -e "JWT_SECRET=$editorSecret" -e 'ALLOW_PRIVATE_IP_ADDRESS=true' onlyoffice/documentserver:9.1
}
if ($LASTEXITCODE -ne 0) { throw '文档服务启动失败，请检查 Docker 输出。' }
$bridgeProcesses=Get-CimInstance Win32_Process -Filter "name='python.exe'" | Where-Object { $_.CommandLine -and $_.CommandLine.Contains('app.online_word:bridge') -and $_.CommandLine.Contains('--port 8001') }
if (-not $bridgeProcesses) {
 Start-Process -FilePath $projectPython -ArgumentList @('-m','uvicorn','app.online_word:bridge','--host','0.0.0.0','--port','8001','--no-access-log') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput editor.stdout.log -RedirectStandardError editor.stderr.log
}
Write-Host '文档服务正在启动，首次下载镜像和初始化可能需要数分钟。'
Write-Host '就绪后在网页生成结果页点击“在线编辑整份 Word”。编辑器内保存后，再返回结果页下载或发送飞书。'
