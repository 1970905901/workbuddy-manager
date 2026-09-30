# WorkBuddy Manager —— 本机启动脚本（Windows / PowerShell）
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# 启用现代 TLS 协议支持
[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12 -bor [System.Net.SecurityProtocolType]::Tls13

# 1. 自动初始化 .env 配置（若首次使用）
$envPath = Join-Path $root '.env'
if (-not (Test-Path $envPath)) {
    $envExample = Join-Path $root '.env.example'
    if (Test-Path $envExample) {
        Write-Host "[INIT] 首次运行，正在自动根据模板生成 .env 配置文件..." -ForegroundColor Cyan
        Copy-Item $envExample $envPath
    }
}

# 2. 自动初始化上游 config.json（若缺少）
$upstreamCfg = Join-Path $root 'upstream\config.json'
if (-not (Test-Path $upstreamCfg)) {
    $exampleCfg = Join-Path $root 'upstream\config.example.json'
    if (Test-Path $exampleCfg) {
        Write-Host "[INIT] 首次运行，正在初始化上游网关 config.json 与随机 API Key..." -ForegroundColor Cyan
        $cfgJson = Get-Content $exampleCfg -Raw | ConvertFrom-Json
        $cfgJson.api_key = 'wbk_' + [System.Guid]::NewGuid().ToString('N')
        $cfgText = $cfgJson | ConvertTo-Json -Depth 10
        [System.IO.File]::WriteAllText($upstreamCfg, $cfgText, (New-Object System.Text.UTF8Encoding($false)))
    }
}

# 确保必要目录存在
$needDirs = @('data', 'upstream\auths', 'upstream\data')
foreach ($nd in $needDirs) {
    $fullNd = Join-Path $root $nd
    if (-not (Test-Path $fullNd)) { New-Item -ItemType Directory -Path $fullNd -Force | Out-Null }
}

# 3. 自动初始化独立 Python 虚拟环境（免装 Python 机制）
$python = Join-Path $root '.venv\Scripts\python.exe'
$uvExe = Join-Path $root '.tools\uv\uv.exe'
if (-not (Test-Path $python)) {
    if (Test-Path $uvExe) {
        Write-Host "[INIT] 首次运行，正在自动构建独立的 Python 3.11 运行环境..." -ForegroundColor Cyan
        & $uvExe venv .venv --python 3.11
        & $uvExe pip install -r (Join-Path $root 'server\requirements.txt') --python $python
    } else {
        Write-Host "[INIT] 正在调用自动更新程序同步依赖与前端产物..." -ForegroundColor Yellow
        & (Join-Path $root 'update.ps1')
    }
}

# 4. 检查前端静态产物，若缺失自动触发同步
$webIndex = Join-Path $root 'web\out\index.html'
if (-not (Test-Path $webIndex)) {
    Write-Host "[WARN] 检测到尚未下载前端页面包，正在调用更新程序自动拉取..." -ForegroundColor Yellow
    & (Join-Path $root 'update.ps1')
}

# 5. 编码设置与参数解析
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$bindHost = '127.0.0.1'
$bindPort = '7864'
if (Test-Path $envPath) {
    foreach ($line in Get-Content $envPath) {
        if ($line -match '^\s*WB_MANAGER_HOST\s*=\s*(\S+)') { $bindHost = $Matches[1] }
        elseif ($line -match '^\s*WB_MANAGER_PORT\s*=\s*(\S+)') { $bindPort = $Matches[1] }
    }
}

# 6. 联动启动上游服务
$upstreamScript = Join-Path $root 'upstream\start-workbuddy2api.cmd'
$upstreamStopScript = Join-Path $root 'upstream\stop-workbuddy2api.cmd'
if (Test-Path $upstreamScript) {
    Write-Host "[INFO] 正在启动上游 workbuddy2api 网关服务..." -ForegroundColor Cyan
    & cmd.exe /c $upstreamScript
}

# 7. 启动主控面板并唤起浏览器
Write-Host "[INFO] WorkBuddy Manager 已就绪: http://${bindHost}:${bindPort}（按 Ctrl+C 退出）" -ForegroundColor Green
Start-Process "http://${bindHost}:${bindPort}"

try {
    & $python -m uvicorn server.main:app --env-file .env --host $bindHost --port $bindPort
} finally {
    if (Test-Path $upstreamStopScript) {
        & cmd.exe /c $upstreamStopScript
    }
}
