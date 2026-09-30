# WorkBuddy Manager —— 跨机器通用自动化更新程序（Windows）
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# 启用现代 TLS 协议支持
[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12 -bor [System.Net.SecurityProtocolType]::Tls13

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "        WorkBuddy Manager 通用自动更新程序        " -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan

# ── 1. 动态自适应探测用户下载路径（毫秒级精准定位，不搜全盘）─
function Get-SmartDownloadDirectories {
    $dirs = [System.Collections.Generic.List[string]]::new()

    # (1) 读取 .env 中用户显式指定的下载目录（若有）
    $envPath = Join-Path $root '.env'
    if (Test-Path $envPath) {
        foreach ($line in Get-Content $envPath) {
            if ($line -match '^\s*WB_DOWNLOAD_DIR\s*=\s*(.+)$') {
                $customDir = $Matches[1].Trim().Trim('"').Trim("'")
                if (Test-Path $customDir) { $dirs.Add($customDir) }
            }
        }
    }

    # (2) 探测 Windows 注册表系统权威下载文件夹（哪怕用户把下载移到了 D/E/F 盘也能精准命中）
    try {
        $regKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders'
        $regVal = (Get-ItemProperty -Path $regKey -ErrorAction SilentlyContinue).'{374DE290-123F-4565-9164-39C4925E467B}'
        if ($regVal) {
            $realDownloads = [System.Environment]::ExpandEnvironmentVariables($regVal)
            if (Test-Path $realDownloads) {
                if (-not $dirs.Contains($realDownloads)) { $dirs.Add($realDownloads) }
                $compSub = Join-Path $realDownloads 'Compressed'
                if ((Test-Path $compSub) -and (-not $dirs.Contains($compSub))) { $dirs.Add($compSub) }
            }
        }
    } catch {}

    # (3) 探测 IDM (Internet Download Manager) 注册表配置目录（若安装过）
    try {
        $idmKey = 'HKCU:\Software\DownloadManager'
        $idmProps = Get-ItemProperty -Path $idmKey -ErrorAction SilentlyContinue
        if ($idmProps) {
            if ($idmProps.SavePathCompressed -and (Test-Path $idmProps.SavePathCompressed) -and (-not $dirs.Contains($idmProps.SavePathCompressed))) {
                $dirs.Add($idmProps.SavePathCompressed)
            }
            if ($idmProps.SavePath -and (Test-Path $idmProps.SavePath) -and (-not $dirs.Contains($idmProps.SavePath))) {
                $dirs.Add($idmProps.SavePath)
            }
        }
    } catch {}

    # (4) 默认 UserProfile 下的 Downloads（作为双保险兜底）
    $defaultDownloads = Join-Path ([Environment]::GetFolderPath('UserProfile')) 'Downloads'
    if (Test-Path $defaultDownloads) {
        if (-not $dirs.Contains($defaultDownloads)) { $dirs.Add($defaultDownloads) }
        $compDef = Join-Path $defaultDownloads 'Compressed'
        if ((Test-Path $compDef) -and (-not $dirs.Contains($compDef))) { $dirs.Add($compDef) }
    }

    # (5) 当前项目所在根目录（支持用户直接拖入或保存在本目录）
    if (-not $dirs.Contains($root)) { $dirs.Add($root) }

    return $dirs
}

function Find-LocalPackage {
    param(
        [string[]]$searchDirectories,
        [string]$targetVersion
    )
    foreach ($d in $searchDirectories) {
        if (-not (Test-Path $d)) { continue }
        $candidates = Get-ChildItem -Path $d -Filter "workbuddy-manager-*.zip" -Recurse -Depth 2 -ErrorAction SilentlyContinue | `
            Where-Object {
                $_.Length -gt 5000000 -and `
                -not (Test-Path "$($_.FullName).crdownload") -and `
                -not (Test-Path "$($_.FullName).tmp")
            } | Sort-Object LastWriteTime -Descending
        
        if ($candidates) {
            if ($targetVersion) {
                $matched = $candidates | Where-Object { $_.Name -match [regex]::Escape($targetVersion) } | Select-Object -First 1
                if ($matched) { return $matched }
            } else {
                return ($candidates | Select-Object -First 1)
            }
        }
    }
    return $null
}

# ── 2. 免 API 限制的版本检测（彻底杜绝 GitHub API 403 频率超限）──
function Get-LatestGitHubTag {
    $proxyList = [System.Collections.Generic.List[string]]::new()
    if ($env:HTTPS_PROXY) { $proxyList.Add($env:HTTPS_PROXY) }
    if ($env:ALL_PROXY -and -not $proxyList.Contains($env:ALL_PROXY)) { $proxyList.Add($env:ALL_PROXY) }
    foreach ($dp in @('http://127.0.0.1:7897', 'http://127.0.0.1:7890', 'http://127.0.0.1:10809')) {
        if (-not $proxyList.Contains($dp)) { $proxyList.Add($dp) }
    }
    $proxyList.Add('')

    $curlExe = (Get-Command curl.exe -ErrorAction SilentlyContinue).Source

    # 方式 1: 直接向 github.com 发起 HEAD 请求取重定向 Location（无 60次/小时 API 限制）
    if ($curlExe) {
        foreach ($p in $proxyList) {
            try {
                $cArgs = @('-s', '-I', '--connect-timeout', '4')
                if ($p) { $cArgs += @('-x', $p) }
                $cArgs += 'https://github.com/ithtelab/workbuddy-manager/releases/latest'
                $lines = & $curlExe $cArgs
                foreach ($line in $lines) {
                    if ($line -match 'location:\s*.*?/releases/tag/([^\r\n/?#]+)') {
                        $foundTag = $Matches[1].Trim()
                        if ($foundTag) {
                            return @{ Tag = $foundTag; Proxy = $p }
                        }
                    }
                }
            } catch {}
        }
    }

    # 方式 2: 通过 git ls-remote 获取最新 tag（按 .NET 真实语义化版本大小排序，无 API 限制）
    try {
        $tags = git ls-remote --tags origin
        if ($tags) {
            $parsedList = @()
            foreach ($line in $tags) {
                if ($line -match 'refs/tags/(v?(\d+\.\d+\.\d+[\w\.\-]*))$') {
                    $rawTag = $Matches[1]
                    $coreVer = $Matches[2].Split('-')[0]
                    try {
                        $parsedList += [PSCustomObject]@{
                            Tag = $rawTag
                            SemVer = [version]$coreVer
                        }
                    } catch {}
                }
            }
            if ($parsedList.Count -gt 0) {
                $best = $parsedList | Sort-Object SemVer -Descending | Select-Object -First 1
                return @{ Tag = $best.Tag; Proxy = '' }
            }
        }
    } catch {}

    # 方式 3: 兜底调用 API
    foreach ($p in $proxyList) {
        try {
            $apiParams = @{
                Uri = "https://api.github.com/repos/ithtelab/workbuddy-manager/releases/latest"
                Headers = @{"User-Agent"="PowerShell"}
                TimeoutSec = 4
            }
            if ($p) { $apiParams['Proxy'] = $p }
            $res = Invoke-RestMethod @apiParams
            if ($res.tag_name) { return @{ Tag = $res.tag_name; Proxy = $p } }
        } catch {}
    }

    return $null
}

# ── 3. 版本比对与执行 ───────────────────────────────────
$currentVer = "未知"
$verFile = Join-Path $root '.version'
if (Test-Path $verFile) {
    $currentVer = (Get-Content $verFile -Raw).Trim()
}
Write-Host "[INFO] 本地当前运行版本: $currentVer" -ForegroundColor White

Write-Host "[INFO] 正在获取 GitHub 官方最新发布版本..."
$tagInfo = Get-LatestGitHubTag
$latestVer = if ($tagInfo) { $tagInfo.Tag } else { $null }
$detectedProxy = if ($tagInfo) { $tagInfo.Proxy } else { if ($env:HTTPS_PROXY) { $env:HTTPS_PROXY } else { 'http://127.0.0.1:7897' } }

$searchDirs = Get-SmartDownloadDirectories
$existingZip = Find-LocalPackage -searchDirectories $searchDirs -targetVersion $null

# 若网络未连通但找到了离线包，从离线包文件名直接推导版本号
if (-not $latestVer -and $existingZip) {
    if ($existingZip.Name -match 'workbuddy-manager-(v?\d+\.\d+\.\d+[\\w\.\-]*)\.zip') {
        $latestVer = $Matches[1]
        Write-Host "[INFO] 已根据离线安装包识别版本: $latestVer" -ForegroundColor Yellow
    } else {
        $latestVer = "离线安装包"
    }
}

# 若网络未连通且没有任何离线包，提供友好交互引导
if (-not $latestVer -and -not $existingZip) {
    Write-Host "[WARN] 无法连接 GitHub 且未在常用目录中找到离线安装包。" -ForegroundColor Yellow
    Write-Host "[INFO] 已检索目录: $($searchDirs -join ' | ')" -ForegroundColor DarkGray
    $openBrowser = Read-Host "是否在浏览器中打开 GitHub Releases 页面手动下载？(Y/n)"
    if ($openBrowser -ne 'n' -and $openBrowser -ne 'N') {
        Start-Process "https://github.com/ithtelab/workbuddy-manager/releases"
        Write-Host "[WAIT] 正在监听下载目录（下载完成后将自动识别并更新）..." -ForegroundColor Cyan
        
        $startTime = [DateTime]::Now
        while ($true) {
            Start-Sleep -Seconds 2
            $detected = Find-LocalPackage -searchDirectories $searchDirs -targetVersion $null
            if ($detected -and ($detected.LastWriteTime -gt $startTime.AddMinutes(-5))) {
                $existingZip = $detected
                if ($detected.Name -match 'workbuddy-manager-(v?\d+\.\d+\.\d+[\w\.\-]*)\.zip') {
                    $latestVer = $Matches[1]
                } else {
                    $latestVer = "离线包"
                }
                break
            }
            if (([DateTime]::Now - $startTime).TotalSeconds -gt 300) {
                Write-Host "[ERROR] 等待下载超时，更新已取消。" -ForegroundColor Red
                exit 1
            }
        }
    } else {
        exit 1
    }
}

Write-Host "[INFO] 官方最新版本: $latestVer" -ForegroundColor Green

if ($currentVer -eq $latestVer) {
    Write-Host "[INFO] 当前已是最新版本 ($currentVer)。" -ForegroundColor Green
    $reinstall = Read-Host "是否强制重新更新覆盖？(y/N)"
    if ($reinstall -ne 'y' -and $reinstall -ne 'Y') {
        Write-Host "[INFO] 操作已安全退出。"
        exit 0
    }
}

# ── 4. 安装包捕获与智能传输 ─────────────────────────────
$tempZip = Join-Path $root "update_temp.zip"
if (Test-Path $tempZip) { Remove-Item $tempZip -Force }

$targetZipFile = Find-LocalPackage -searchDirectories $searchDirs -targetVersion $latestVer

if ($targetZipFile) {
    Write-Host "[INFO] 已在目录中精准识别到安装包: $($targetZipFile.FullName)" -ForegroundColor Green
    Write-Host "[INFO] 正在移动到项目目录进行解压更新..." -ForegroundColor Cyan
    Move-Item -Path $targetZipFile.FullName -Destination $tempZip -Force
} else {
    $downloadUrl = "https://github.com/ithtelab/workbuddy-manager/releases/download/$latestVer/workbuddy-manager-$latestVer.zip"
    $curlExe = (Get-Command curl.exe -ErrorAction SilentlyContinue).Source
    $downloadSuccess = $false

    # 尝试方式 1: 使用 curl.exe 命令行极速拉取
    if ($curlExe) {
        Write-Host "[INFO] 正在尝试后台极速下载..." -ForegroundColor Cyan
        $curlArgs = @('-L', '--fail', '--connect-timeout', '10', '-o', $tempZip)
        if ($detectedProxy) { $curlArgs += @('-x', $detectedProxy) }
        $curlArgs += $downloadUrl

        & $curlExe $curlArgs
        if ($LASTEXITCODE -eq 0 -and (Test-Path $tempZip) -and ((Get-Item $tempZip).Length -gt 5000000)) {
            $downloadSuccess = $true
            Write-Host "[SUCCESS] 后台下载完成并校验通过。" -ForegroundColor Green
        }
    }

    # 尝试方式 2: 若后台网络阻塞，自动唤起浏览器下载并自动监听下载目录
    if (-not $downloadSuccess) {
        Write-Host "[WARN] 命令行下载受阻，自动为您在浏览器中打开下载直链..." -ForegroundColor Yellow
        Write-Host "[INFO] 下载直链: $downloadUrl" -ForegroundColor Cyan
        Start-Process $downloadUrl

        Write-Host "[WAIT] 正在实时监听下载目录（支持任何盘符下的 Downloads / Compressed 等）..." -ForegroundColor Cyan
        Write-Host "[WAIT] 浏览器下载完成后本程序将全自动识别并完成更新，无需手动拖拽。" -ForegroundColor DarkGray

        $watchTimeoutSeconds = 300
        $startTime = [DateTime]::Now
        $promptedManual = $false

        while (-not (Test-Path $tempZip)) {
            Start-Sleep -Seconds 2
            
            if (-not $promptedManual -and ([DateTime]::Now - $startTime).TotalSeconds -gt 30) {
                $promptedManual = $true
                Write-Host "[TIP] 如果您的浏览器下载到了特殊位置，可直接将其拖入本目录，或在稍后提示时粘贴路径。" -ForegroundColor Yellow
            }

            if (([DateTime]::Now - $startTime).TotalSeconds -gt $watchTimeoutSeconds) {
                Write-Host "[PROMPT] 未在常用目录检测到下载包。" -ForegroundColor Yellow
                $customInput = Read-Host "请输入您的自定义下载文件夹路径 (按回车退出)"
                if ($customInput -and (Test-Path $customInput)) {
                    $envPath = Join-Path $root '.env'
                    if (Test-Path $envPath) {
                        Add-Content -Path $envPath -Value "`nWB_DOWNLOAD_DIR=$customInput"
                    }
                    $searchDirs = Get-SmartDownloadDirectories
                    $foundCustom = Find-LocalPackage -searchDirectories @($customInput) -targetVersion $latestVer
                    if ($foundCustom) {
                        Move-Item -Path $foundCustom.FullName -Destination $tempZip -Force
                        break
                    }
                }
                Write-Host "[ERROR] 等待下载超时，操作已取消。" -ForegroundColor Red
                exit 1
            }

            $detected = Find-LocalPackage -searchDirectories $searchDirs -targetVersion $latestVer
            if ($detected -and ($detected.LastWriteTime -gt $startTime.AddMinutes(-5))) {
                try {
                    $stream = [System.IO.File]::Open($detected.FullName, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::None)
                    $stream.Dispose()
                    Write-Host "[SUCCESS] 检测到下载完成: $($detected.FullName)" -ForegroundColor Green
                    Write-Host "[INFO] 自动将其剪切移动到项目目录..." -ForegroundColor Cyan
                    Move-Item -Path $detected.FullName -Destination $tempZip -Force
                    break
                } catch {}
            }
        }
    }
}

# ── 5. 停止运行中服务 ───────────────────────────────────
Write-Host "[INFO] 正在安全停止当前服务进程..." -ForegroundColor Yellow
$stopScript = Join-Path $root 'stop.ps1'
if (Test-Path $stopScript) { & $stopScript }

# ── 6. 备份本地 Windows 适配脚本 ─────────────────────────
$backupDir = Join-Path $root '.tools\scripts_backup'
New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
$scriptsToProtect = @('start.ps1', 'stop.ps1', 'service-tools.ps1', 'start.cmd', 'stop.cmd', 'update.cmd', 'update.ps1')
foreach ($s in $scriptsToProtect) {
    $src = Join-Path $root $s
    if (Test-Path $src) { Copy-Item $src (Join-Path $backupDir $s) -Force }
}

# ── 7. 同步 Git 源码分支（若存在 Git 仓库）─────────────────
if (Test-Path (Join-Path $root '.git')) {
    try {
        git fetch origin
        git reset --hard origin/main
    } catch {
        Write-Host "[WARN] Git 源码同步提示: $($_.Exception.Message)" -ForegroundColor DarkGray
    }
}

# ── 8. 安全解压并覆盖 ───────────────────────────────────
Write-Host "[INFO] 正在更新前端静态资源 (web/out) 与服务端核心文件..." -ForegroundColor Cyan

# 清空旧的前端静态编译目录，避免历史版本残留
$webOut = Join-Path $root 'web\out'
if (Test-Path $webOut) { Remove-Item $webOut -Recurse -Force }

Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::OpenRead($tempZip)

foreach ($entry in $zip.Entries) {
    $fullName = $entry.FullName
    $relPath = $null
    if ($fullName -match '^workbuddy-manager-[^/]+/(.+)$') {
        $relPath = $Matches[1]
    } elseif ($fullName -notmatch '^[^/]+/') {
        $relPath = $fullName
    }

    if (-not $relPath) { continue }

    # 保护项：绝不覆盖用户个人配置文件及独立环境
    if ($relPath -eq '.env' -or `
        $relPath.StartsWith('data/') -or `
        $relPath.StartsWith('.venv/') -or `
        $relPath.StartsWith('.tools/') -or `
        $relPath.StartsWith('upstream/auths/') -or `
        $relPath -eq 'upstream/config.json' -or `
        $relPath -eq 'upstream/wb2api.exe') {
        continue
    }

    # 允许更新的产物
    if ($relPath.StartsWith('web/out/') -or `
        $relPath.StartsWith('server/') -or `
        $relPath.StartsWith('docs/') -or `
        $relPath.StartsWith('deploy/') -or `
        $relPath -eq '.version' -or `
        $relPath -eq 'CHANGELOG.md' -or `
        $relPath -eq 'README.md' -or `
        $relPath -eq 'README.en.md') {
        $destPath = Join-Path $root $relPath.Replace('/', '\')
        if ($entry.FullName.EndsWith('/')) {
            if (-not (Test-Path $destPath)) { [System.IO.Directory]::CreateDirectory($destPath) | Out-Null }
        } else {
            $dir = Split-Path $destPath -Parent
            if (-not (Test-Path $dir)) { [System.IO.Directory]::CreateDirectory($dir) | Out-Null }
            [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $destPath, $true)
        }
    }
}
$zip.Dispose()

# 更新本地 .version 标记文件
Set-Content -Path (Join-Path $root '.version') -Value $latestVer -Encoding UTF8

# 彻底清理临时 zip 包，绝不遗留磁盘垃圾
Remove-Item $tempZip -Force -ErrorAction SilentlyContinue
Write-Host "[INFO] 临时安装包已自动彻底清理删除。" -ForegroundColor DarkGray

# 还原 Windows 脚本与带空格路径引号修复
foreach ($s in $scriptsToProtect) {
    $bak = Join-Path $backupDir $s
    if (Test-Path $bak) { Copy-Item $bak (Join-Path $root $s) -Force }
}
Remove-Item $backupDir -Recurse -Force -ErrorAction SilentlyContinue

$upstreamCmds = @('upstream\start-workbuddy2api.cmd', 'upstream\status-workbuddy2api.cmd', 'upstream\stop-workbuddy2api.cmd')
foreach ($ucmd in $upstreamCmds) {
    $fullCmd = Join-Path $root $ucmd
    if (Test-Path $fullCmd) {
        $content = [System.IO.File]::ReadAllText($fullCmd)
        $content = $content.Replace('-FilePath $env:WB2API_EXE', '-FilePath \"$env:WB2API_EXE\"')
        $content = $content.Replace('-WorkingDirectory $env:WB2API_ROOT', '-WorkingDirectory \"$env:WB2API_ROOT\"')
        $content = $content.Replace('[IO.Path]::GetFullPath($env:WB2API_EXE)', '[IO.Path]::GetFullPath(\"$env:WB2API_EXE\")')
        [System.IO.File]::WriteAllText($fullCmd, $content)
    }
}

# ── 9. 同步 Python 运行环境依赖 ─────────────────────────
Write-Host "[INFO] 正在同步 Python 依赖库..." -ForegroundColor Cyan
$uvExe = Join-Path $root '.tools\uv\uv.exe'
$reqFile = Join-Path $root 'server\requirements.txt'
$venvPython = Join-Path $root '.venv\Scripts\python.exe'

if ((Test-Path $uvExe) -and (Test-Path $venvPython) -and (Test-Path $reqFile)) {
    & $uvExe pip install -r $reqFile --python $venvPython
}

Write-Host "==================================================" -ForegroundColor Green
Write-Host " [SUCCESS] WorkBuddy Manager 已成功更新至 $latestVer " -ForegroundColor Green
Write-Host "==================================================" -ForegroundColor Green
Write-Host "[INFO] 个人配置（.env、账号授权、数据库历史）完好无损。" -ForegroundColor White

$startNow = Read-Host "是否立即启动服务？(Y/n)"
if ($startNow -ne 'n' -and $startNow -ne 'N') {
    Write-Host "[INFO] 正在启动服务..." -ForegroundColor Cyan
    $startCmd = Join-Path $root 'start.cmd'
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "`"$startCmd`""
}
