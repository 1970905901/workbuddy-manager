# WorkBuddy Manager —— 启动前环境准备（Windows / PowerShell）
# 幂等：已就绪的步骤直接跳过。start.ps1 与桌面启动器在启动前都会调用本脚本。
$ErrorActionPreference = 'Stop'
# 脚本住在 windows\ 子目录里：项目根（server/、web/out、.env、upstream/ 所在处）
# 是它的上一级。拼项目内的路径一律用 $root；只有找**同一目录里的兄弟脚本**才用 $scriptDir。
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $scriptDir
Set-Location $root

# 启用现代 TLS 协议支持
[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12 -bor [System.Net.SecurityProtocolType]::Tls13

# 1. 自动初始化 .env 配置（若首次使用）
$envPath = Join-Path $root '.env'
if (-not (Test-Path $envPath)) {
    $envExample = Join-Path $root '.env.example'
    if (Test-Path $envExample) {
        Write-Host "[INIT] 首次运行，正在自动根据模板生成适配 Windows 的 .env 配置文件..." -ForegroundColor Cyan
        $envContent = Get-Content $envExample -Raw -Encoding UTF8
        $rootSlash = ($root -replace '\\', '/')

        # 将 Linux /opt 模板路径自动转换为 Windows 本地路径，并启用 native 模式
        $envContent = $envContent.Replace('/opt/workbuddy2api/auths', "$rootSlash/upstream/auths")
        $envContent = $envContent.Replace('/opt/workbuddy2api/config.json', "$rootSlash/upstream/config.json")
        $envContent = $envContent.Replace('/opt/workbuddy2api/start-workbuddy2api.cmd', "$rootSlash/upstream/start-workbuddy2api.cmd")
        $envContent = $envContent.Replace('/opt/workbuddy2api/stop-workbuddy2api.cmd', "$rootSlash/upstream/stop-workbuddy2api.cmd")
        $envContent = $envContent.Replace('/opt/workbuddy2api/data/server.err.log', "$rootSlash/upstream/data/server.err.log")
        $envContent = $envContent.Replace('/opt/workbuddy2api', "$rootSlash/upstream")
        $envContent = $envContent.Replace('/opt/workbuddy-manager/data/manager.db', "$rootSlash/data/manager.db")
        $envContent = $envContent.Replace('/opt/workbuddy-manager/data/users.json', "$rootSlash/data/users.json")
        $envContent = $envContent.Replace('/opt/workbuddy-manager/data', "$rootSlash/data")
        $envContent = $envContent.Replace('/opt/workbuddy-manager/web/out', "$rootSlash/web/out")
        $envContent = $envContent.Replace('WB2API_MODE=docker', 'WB2API_MODE=native')
        $envContent = $envContent.Replace('WB_MANAGER_HOST=0.0.0.0', 'WB_MANAGER_HOST=127.0.0.1')
        $envContent = $envContent.Replace('WB_SECURE_COOKIE=auto', 'WB_SECURE_COOKIE=false')

        # 无 BOM：模板首行虽然目前是注释（BOM 落在注释里无害），但一旦有人调整模板顺序，
        # 第一个变量名就会带 BOM 前缀而整个失效 —— 不给自己留这种地雷。
        [System.IO.File]::WriteAllText($envPath, $envContent, (New-Object System.Text.UTF8Encoding($false)))
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

# 3. 确保必要目录存在
$needDirs = @('data', 'upstream\auths', 'upstream\data')
foreach ($nd in $needDirs) {
    $fullNd = Join-Path $root $nd
    if (-not (Test-Path $fullNd)) { New-Item -ItemType Directory -Path $fullNd -Force | Out-Null }
}

# 4. 准备 Python 环境：预置运行时优先；其次 uv 建独立虚拟环境；最后回退自动更新流程
. (Join-Path $scriptDir 'python-env.ps1')
$python = Get-WbPython -Root $root
if (-not $python) {
    $uvExe = Join-Path $root '.tools\uv\uv.exe'
    if (Test-Path $uvExe) {
        Write-Host "[INIT] 首次运行，正在自动构建独立的 Python 3.11 运行环境..." -ForegroundColor Cyan
        & $uvExe venv .venv --python 3.11
        $python = Join-Path $root '.venv\Scripts\python.exe'
        if (-not (Test-Path $python)) { throw "[ERROR] 虚拟环境创建失败，请检查网络后重试（windows\prepare.ps1）" }
        & $uvExe pip install -r (Join-Path $root 'server\requirements.txt') --python $python
    } else {
        Write-Host "[INIT] 正在调用自动更新程序同步依赖与前端产物..." -ForegroundColor Yellow
        & (Join-Path $scriptDir 'update.ps1')
        $python = Get-WbPython -Root $root
    }
}

# 5. 依赖健全性检查：关键包缺了就补装（预置运行时被破坏、依赖未装全等）
if ($python) {
    & $python -c "import fastapi, uvicorn" 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[INIT] 检测到 Python 依赖不完整，正在安装 requirements.txt ..." -ForegroundColor Yellow
        $reqFile = Join-Path $root 'server\requirements.txt'
        $uvExe = Join-Path $root '.tools\uv\uv.exe'
        if (Test-Path $uvExe) { & $uvExe pip install -r $reqFile --python $python }
        else { & $python -m pip install -r $reqFile }
    }
}

# 6. 检查前端静态产物，若缺失自动触发同步
$webIndex = Join-Path $root 'web\out\index.html'
if (-not (Test-Path $webIndex)) {
    Write-Host "[WARN] 检测到尚未下载前端页面包，正在调用更新程序自动拉取..." -ForegroundColor Yellow
    & (Join-Path $scriptDir 'update.ps1')
}

Write-Host "[PREP] 环境准备完成。" -ForegroundColor Green
