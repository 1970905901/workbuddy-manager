# WorkBuddy Manager —— Windows 安装包构建脚本
#
# 从源码构建单文件安装器 WorkBuddyManagerSetup.exe（内嵌可运行的项目、
# 预置 Python 运行时与上游网关二进制，目标机零环境要求）。
#
# 用法（在仓库根目录）：
#   powershell -ExecutionPolicy Bypass -File windows\launcher\build.ps1
# 可选参数：
#   -SkipUpstream  不打包上游网关（跳过源码下载与 wb2api.exe 编译）
#   -SkipRuntime   不预置 Python 运行时（改内嵌 uv.exe，首次启动联网准备）
#   -ForceWeb      强制重新构建前端产物
param(
    [switch]$SkipUpstream,
    [switch]$SkipRuntime,
    [switch]$ForceWeb
)

$ErrorActionPreference = 'Stop'
# 脚本住在 windows\launcher\，仓库根是它的上两级
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent (Split-Path -Parent $scriptDir)
$distDir = Join-Path $scriptDir 'dist'
$stageDir = Join-Path $env:TEMP ('wb-payload-' + [Guid]::NewGuid().ToString('N'))

$UpstreamSrcUrl = 'https://github.com/ithtelab/workbuddy-manager/releases/download/upstream-src/workbuddy2api-src.tar.gz'
$UvUrl = 'https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip'
$GoToolchainUrl = 'https://go.dev/dl/go1.27.0.windows-amd64.zip'
# 显式用 Windows 自带 bsdtar：PATH 里的 GNU tar 会把 C:\ 路径当作「远程主机:路径」
$TarExe = Join-Path $env:WINDIR 'System32\tar.exe'

function Write-Step($message) { Write-Host ('[BUILD] ' + $message) -ForegroundColor Cyan }
function Write-Warn2($message) { Write-Host ('[WARN]  ' + $message) -ForegroundColor Yellow }

function Invoke-Curl($url, $outPath) {
    & curl.exe -sL --fail --connect-timeout 20 -o $outPath $url
    if ($LASTEXITCODE -ne 0) { throw ('下载失败：' + $url) }
}

# robocopy 的退出码 0-7 都属于成功（1 表示复制了文件），8 起才是错误
function Copy-Tree($source, $destination, $excludeDirs = @(), $excludeFiles = @()) {
    $rcArgs = @($source, $destination, '/E', '/NFL', '/NDL', '/NJH', '/NJS', '/NP')
    foreach ($d in $excludeDirs) { $rcArgs += @('/XD', $d) }
    foreach ($f in $excludeFiles) { $rcArgs += @('/XF', $f) }
    & robocopy @rcArgs | Out-Null
    if ($LASTEXITCODE -ge 8) { throw ('robocopy 失败（' + $source + ' → ' + $destination + '，退出码 ' + $LASTEXITCODE + '）') }
}

# 预装依赖 + 运行时瘦身（网络下载与本机复用两条路径共用）
function Prepare-PythonRuntime($pyDir) {
    Write-Step '预装 Python 依赖（server\requirements.txt）…'
    & (Join-Path $pyDir 'python.exe') -m pip install --no-warn-script-location `
        --disable-pip-version-check -q -r (Join-Path $repoRoot 'server\requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Python 依赖预装失败' }
    Trim-PythonRuntime $pyDir
}

# 扫描本机 uv 管理的 Python（新旧默认位置都查），返回 3.11+ 中版本最高的一个
function Find-LocalUvPython {
    $roots = @(
        (Join-Path $env:APPDATA 'uv\python'),
        (Join-Path $env:LOCALAPPDATA 'uv\python')
    )
    if ($env:UV_PYTHON_INSTALL_DIR) { $roots = @($env:UV_PYTHON_INSTALL_DIR) + $roots }
    $best = $null
    $bestVer = $null
    foreach ($root in $roots) {
        if (-not (Test-Path $root)) { continue }
        foreach ($item in (Get-ChildItem $root -Directory -ErrorAction SilentlyContinue)) {
            if ($item.Name -notmatch '^cpython-3\.(\d+)\.(\d+)') { continue }
            if ([int]$Matches[1] -lt 11) { continue }
            if (-not (Test-Path (Join-Path $item.FullName 'python.exe'))) { continue }
            $ver = [version]('3.' + $Matches[1] + '.' + $Matches[2])
            if ($null -eq $bestVer -or $ver -gt $bestVer) { $best = $item.FullName; $bestVer = $ver }
        }
    }
    return $best
}

# 找本机已装的 uv.exe（常见安装位置）
function Find-LocalUv {
    $cmd = Get-Command uv.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($p in @(
            (Join-Path $env:USERPROFILE '.local\bin\uv.exe'),
            (Join-Path $env:LOCALAPPDATA 'Programs\uv\uv.exe'),
            (Join-Path $env:USERPROFILE '.cargo\bin\uv.exe'))) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

# ── 1. 前端静态产物 ─────────────────────────────────────────
$webIndex = Join-Path $repoRoot 'web\out\index.html'
if ($ForceWeb -or -not (Test-Path $webIndex)) {
    Write-Step '构建前端静态产物（npm ci + build:export）…'
    Push-Location (Join-Path $repoRoot 'web')
    try {
        # npm ci 失败的常见原因：配置的缓存目录（node_cache）被杀软锁定/无权限（EPERM）、
        # 实时扫描干扰 node_modules 解包（TAR_ENTRY_ERROR）——换干净临时缓存并清掉
        # 半损坏的 node_modules 后重试一次
        & npm ci --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) {
            Write-Warn2 'npm ci 失败（缓存目录可能被占用或杀软锁定），改用临时缓存重试…'
            if (Test-Path node_modules) {
                Remove-Item node_modules -Recurse -Force -ErrorAction SilentlyContinue
            }
            $fallbackCache = Join-Path $env:TEMP ('wb-npm-cache-' + [Guid]::NewGuid().ToString('N'))
            & npm ci --no-audit --no-fund --cache $fallbackCache
            if ($LASTEXITCODE -ne 0) { throw 'npm ci 失败（临时缓存重试后仍失败，请检查杀软对项目目录与 npm 缓存目录的实时扫描设置）' }
        }
        & npm run build:export
        if ($LASTEXITCODE -ne 0) { throw '前端构建失败' }
    } finally { Pop-Location }
    if (-not (Test-Path $webIndex)) { throw '前端产物缺失：web\out\index.html' }
} else {
    Write-Step '前端静态产物已存在，跳过构建'
}

# 运行时瘦身：目标机用不到的标准库与调试符号全部去掉
function Trim-PythonRuntime($pyDir) {
    foreach ($name in @('test', 'idlelib', 'lib2to3', 'turtledemo', 'ensurepip')) {
        $p = Join-Path $pyDir ('Lib\' + $name)
        if (Test-Path $p) { Remove-Item $p -Recurse -Force }
    }
    Get-ChildItem $pyDir -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Get-ChildItem $pyDir -Recurse -File -Include '*.pdb' -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue
    $sitePackages = Join-Path $pyDir 'Lib\site-packages'
    if (Test-Path $sitePackages) {
        Get-ChildItem $sitePackages -Recurse -Directory -Filter 'tests' -ErrorAction SilentlyContinue |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        Get-ChildItem $sitePackages -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    }
}
# python-build-standalone 的 tag 每次发版都变：优先动态解析 latest，失败退回固定版本
function Resolve-PythonRuntimeUrl {
    $fallback = 'https://github.com/astral-sh/python-build-standalone/releases/download/20261009/cpython-3.11.17%2B20261009-x86_64-pc-windows-msvc-install_only_stripped.tar.gz'
    try {
        $latest = & curl.exe -s --connect-timeout 10 'https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest'
        if ($LASTEXITCODE -eq 0 -and $latest) {
            $joined = ($latest -join ' ')
            $m = [regex]::Match($joined, '"browser_download_url":\s*"(https://[^"]+cpython-3\.11[^"]*x86_64-pc-windows-msvc-install_only_stripped\.tar\.gz)"')
            if ($m.Success) { return $m.Groups[1].Value }
        }
    } catch { }
    return $fallback
}
# 本机 Go 优先；没有则下载便携工具链到 .tools\go\（一次性，之后复用）
function Find-GoToolchain {
    $go = Get-Command go.exe -ErrorAction SilentlyContinue
    if ($go) { return $go.Source }
    $portable = Join-Path $repoRoot '.tools\go\bin\go.exe'
    if (Test-Path $portable) { return $portable }
    Write-Step '本机未安装 Go，下载便携工具链（约 80MB）…'
    $zip = Join-Path $env:TEMP 'wb-go-portable.zip'
    Invoke-Curl $GoToolchainUrl $zip
    New-Item -ItemType Directory (Join-Path $repoRoot '.tools') -Force | Out-Null
    Expand-Archive $zip (Join-Path $repoRoot '.tools') -Force
    Remove-Item $zip -Force
    if (-not (Test-Path $portable)) { throw '便携 Go 解压失败' }
    return $portable
}
$webIndex = Join-Path $repoRoot 'web\out\index.html'
if ($ForceWeb -or -not (Test-Path $webIndex)) {
    Write-Step '构建前端静态产物（npm ci + build:export）…'
    Push-Location (Join-Path $repoRoot 'web')
    try {
        & npm ci
        if ($LASTEXITCODE -ne 0) { throw 'npm ci 失败' }
        & npm run build:export
        if ($LASTEXITCODE -ne 0) { throw '前端构建失败' }
    } finally { Pop-Location }
    if (-not (Test-Path $webIndex)) { throw '前端产物缺失：web\out\index.html' }
} else {
    Write-Step '前端静态产物已存在，跳过构建'
}

# ── 2. 上游源码与 wb2api.exe ────────────────────────────────
$upstreamDir = Join-Path $repoRoot 'upstream'
if (-not $SkipUpstream) {
    if (-not (Test-Path (Join-Path $upstreamDir 'go.mod'))) {
        Write-Step '下载上游源码包…'
        $tarPath = Join-Path $env:TEMP 'wb-upstream-src.tar.gz'
        Invoke-Curl $UpstreamSrcUrl $tarPath
        $extractTmp = Join-Path $env:TEMP ('wb-upstream-' + [Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory $extractTmp -Force | Out-Null
        & $TarExe -xzf $tarPath -C $extractTmp
        if ($LASTEXITCODE -ne 0) { throw '上游源码解压失败' }
        New-Item -ItemType Directory $upstreamDir -Force | Out-Null
        Move-Item (Join-Path $extractTmp 'workbuddy2api\*') $upstreamDir -Force
        Remove-Item $extractTmp -Recurse -Force
        Remove-Item $tarPath -Force
    } else {
        Write-Step '上游源码已存在，跳过下载'
    }

    if (-not (Test-Path (Join-Path $upstreamDir 'wb2api.exe'))) {
        Write-Step '编译上游网关 wb2api.exe（Go，-ldflags "-s -w"）…'
        $goExe = Find-GoToolchain
        Push-Location $upstreamDir
        try {
            & $goExe build -ldflags '-s -w' -trimpath -o wb2api.exe ./cmd/server
            if ($LASTEXITCODE -ne 0) { throw 'wb2api.exe 编译失败' }
        } finally { Pop-Location }
    } else {
        Write-Step 'wb2api.exe 已存在，跳过编译'
    }
}


# ── 3. Python 运行时（网络预置 → 本机复用 → uv 兜底）────────
$runtimeOk = $false
$pyRoot = $null
$uvExeStaged = $null
if (-not $SkipRuntime) {
    # 3a. 网络下载 python-build-standalone（标准路径，可复现）
    try {
        $url = Resolve-PythonRuntimeUrl
        Write-Step ('下载 Python 3.11 独立运行时（stripped）：' + $url)
        $tarPath = Join-Path $env:TEMP 'wb-python-runtime.tar.gz'
        Invoke-Curl $url $tarPath
        $pyStage = Join-Path $env:TEMP ('wb-python-' + [Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory $pyStage -Force | Out-Null
        & $TarExe -xzf $tarPath -C $pyStage
        if ($LASTEXITCODE -ne 0) { throw 'Python 运行时解压失败' }
        Remove-Item $tarPath -Force
        $pyRoot = Join-Path $pyStage 'python'
        if (-not (Test-Path (Join-Path $pyRoot 'python.exe'))) { throw 'Python 运行时结构异常（缺少 python.exe）' }
        Prepare-PythonRuntime $pyRoot
        $runtimeOk = $true
    } catch {
        Write-Warn2 $_.Exception.Message
        $pyRoot = $null
    }

    # 3b. 本机回退：复用 uv 管理的 Python（离线构建场景）
    if (-not $runtimeOk) {
        try {
            $localPy = Find-LocalUvPython
            if ($localPy) {
                Write-Step ('网络不可用，复用本机 uv 管理的 Python：' + $localPy)
                $pyStage = Join-Path $env:TEMP ('wb-python-' + [Guid]::NewGuid().ToString('N'))
                New-Item -ItemType Directory $pyStage -Force | Out-Null
                Copy-Tree $localPy (Join-Path $pyStage 'python')
                $pyRoot = Join-Path $pyStage 'python'
                Prepare-PythonRuntime $pyRoot
                $runtimeOk = $true
            }
        } catch {
            Write-Warn2 $_.Exception.Message
            $pyRoot = $null
        }
    }

    if (-not $runtimeOk) {
        Write-Warn2 'Python 运行时预置失败，安装包将改用 uv.exe 兜底（首次启动需联网准备环境）'
    }
}
if (-not $runtimeOk) {
    # 3c. uv.exe 兜底：本机已装的优先，网络下载次之
    $uvSource = Find-LocalUv
    if ($uvSource) {
        Write-Step ('使用本机已有的 uv.exe 作为兜底运行时准备工具：' + $uvSource)
        $uvExeStaged = $uvSource
    } else {
        Write-Step '下载 uv.exe（兜底运行时准备工具）…'
        $uvZip = Join-Path $env:TEMP 'wb-uv.zip'
        Invoke-Curl $UvUrl $uvZip
        $uvTmp = Join-Path $env:TEMP ('wb-uv-' + [Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory $uvTmp -Force | Out-Null
        Expand-Archive $uvZip $uvTmp -Force
        Remove-Item $uvZip -Force
        $uvExeStaged = Join-Path $uvTmp 'uv.exe'
        if (-not (Test-Path $uvExeStaged)) { throw 'uv.exe 解压失败' }
    }
}



# ── 4. 组装 payload 暂存目录 ────────────────────────────────
Write-Step '组装 payload…'
New-Item -ItemType Directory $stageDir -Force | Out-Null

Copy-Tree (Join-Path $repoRoot 'server') (Join-Path $stageDir 'server') @('__pycache__') @('*.pyc')
Copy-Tree (Join-Path $repoRoot 'web\out') (Join-Path $stageDir 'web\out')
Copy-Tree (Join-Path $repoRoot 'deploy') (Join-Path $stageDir 'deploy')

# windows\ 只带运行所需脚本（不携带 launcher 构建工具自身）
$winScripts = @('start.cmd', 'stop.cmd', 'update.cmd', 'start.ps1', 'stop.ps1', 'update.ps1',
    'service-tools.ps1', 'prepare.ps1', 'python-env.ps1', 'README.md')
$stageWindows = Join-Path $stageDir 'windows'
New-Item -ItemType Directory $stageWindows -Force | Out-Null
foreach ($name in $winScripts) {
    $source = Join-Path (Join-Path $repoRoot 'windows') $name
    if (Test-Path $source) { Copy-Item $source $stageWindows -Force }
}

# 用户文档白名单（与 release.yml 的 docs-pack 名单保持一致）
$docsList = @('usage.md', 'features.md', 'screenshots.md', 'development.md', 'security-notes.md',
    'api-tokens.md', 'account-proxy-routes.md', 'SECURITY-AUDIT.md', 'release-signing.md')
$stageDocs = Join-Path $stageDir 'docs'
New-Item -ItemType Directory $stageDocs -Force | Out-Null
foreach ($name in $docsList) {
    $source = Join-Path (Join-Path $repoRoot 'docs') $name
    if (Test-Path $source) { Copy-Item $source $stageDocs -Force }
    else { Write-Warn2 ('用户文档缺失：docs\' + $name) }
}
if (Test-Path (Join-Path $repoRoot 'docs\images')) {
    Copy-Tree (Join-Path $repoRoot 'docs\images') (Join-Path $stageDocs 'images')
}

foreach ($name in @('.env.example', 'README.md', 'README.en.md', 'CHANGELOG.md', 'LICENSE',
        'Dockerfile', 'docker-compose.yml')) {
    $source = Join-Path $repoRoot $name
    if (Test-Path $source) { Copy-Item $source $stageDir -Force }
    else { Write-Warn2 ('根文件缺失：' + $name) }
}

# 版本标记（面板与更新脚本读取）
$version = (& git describe --tags --always 2>$null)
if (-not $version) { $version = 'dev' }
[System.IO.File]::WriteAllText((Join-Path $stageDir '.version'), $version, (New-Object System.Text.UTF8Encoding($false)))

# 将 git 版本转换为 Windows 可接受的四段数字版本：
# v1.0.85-3-gabcdef → 1.0.85.3；无可解析版本时使用 0.0.0.0。
function ConvertTo-AssemblyVersion($gitVersion) {
    if ($gitVersion -match '^v?(\d+)\.(\d+)\.(\d+)(?:-(\d+)-g[0-9a-f]+)?') {
        $revision = if ($Matches[4]) { [int]$Matches[4] } else { 0 }
        return ($Matches[1] + '.' + $Matches[2] + '.' + $Matches[3] + '.' + $revision)
    }
    return '0.0.0.0'
}
$assemblyVersion = ConvertTo-AssemblyVersion $version

if (-not $SkipUpstream) {
    Copy-Tree $upstreamDir (Join-Path $stageDir 'upstream') @('.git', '__pycache__')
}
if ($runtimeOk) {
    Copy-Tree $pyRoot (Join-Path $stageDir '.runtime\python')
} else {
    $uvDir = Join-Path $stageDir '.tools\uv'
    New-Item -ItemType Directory $uvDir -Force | Out-Null
    Copy-Item $uvExeStaged (Join-Path $uvDir 'uv.exe') -Force
}

# ── 5. 压缩 payload ─────────────────────────────────────────
Write-Step '压缩 payload…'
$zipPath = Join-Path $env:TEMP 'wb-payload.zip'
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
Add-Type -AssemblyName System.IO.Compression.FileSystem | Out-Null
[System.IO.Compression.ZipFile]::CreateFromDirectory(
    $stageDir, $zipPath, [System.IO.Compression.CompressionLevel]::Optimal, $false)

# ── 6. 编译安装器 exe（payload 作为资源内嵌）────────────────
Write-Step '编译安装器 exe（csc）…'
$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path $csc)) { $csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe' }
if (-not (Test-Path $csc)) { throw '未找到 csc.exe（需要 .NET Framework 4.x 自带编译器）' }

$iconPath = Join-Path $repoRoot 'web\public\favicon\favicon.ico'
if (-not (Test-Path $iconPath)) { throw '缺少图标文件：web\public\favicon\favicon.ico' }

New-Item -ItemType Directory $distDir -Force | Out-Null
$outExe = Join-Path $distDir 'WorkBuddyManagerSetup.exe'
if (Test-Path $outExe) { Remove-Item $outExe -Force }

# 生成程序集元数据，写入 exe 的文件属性版本号
$assemblyInfoPath = Join-Path $env:TEMP ('wb-assembly-info-' + [Guid]::NewGuid().ToString('N') + '.cs')
$assemblyInfo = @"
using System.Reflection;
[assembly: AssemblyTitle("WorkBuddy Manager")]
[assembly: AssemblyProduct("WorkBuddy Manager")]
[assembly: AssemblyCompany("WorkBuddy Manager")]
[assembly: AssemblyVersion("$assemblyVersion")]
[assembly: AssemblyFileVersion("$assemblyVersion")]
[assembly: AssemblyInformationalVersion("$version")]
"@
[System.IO.File]::WriteAllText($assemblyInfoPath, $assemblyInfo, (New-Object System.Text.UTF8Encoding($false)))

$sourceFiles = @(Get-ChildItem (Join-Path $scriptDir 'src\*.cs') | ForEach-Object { $_.FullName })
if ($sourceFiles.Count -eq 0) { throw '未找到 C# 源文件：windows\launcher\src\*.cs' }
$sources = $sourceFiles + $assemblyInfoPath

& $csc -nologo -target:winexe -codepage:65001 -optimize+ `
    ('-win32icon:' + $iconPath) `
    ('-resource:' + $zipPath + ',payload.zip') `
    ('-out:' + $outExe) `
    -reference:System.dll,System.Core.dll,System.Drawing.dll,System.Windows.Forms.dll,System.IO.Compression.dll,System.IO.Compression.FileSystem.dll,Microsoft.CSharp.dll `
    $sources
if ($LASTEXITCODE -ne 0) { throw 'csc 编译失败' }
Remove-Item $assemblyInfoPath -Force -ErrorAction SilentlyContinue

# ── 7. 报告与清理 ───────────────────────────────────────────
$zipMB = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
$exeMB = [math]::Round((Get-Item $outExe).Length / 1MB, 1)
Remove-Item $stageDir -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ''
Write-Host '==================================' -ForegroundColor Green
Write-Host ' 构建完成' -ForegroundColor Green
Write-Host (' 安装包：' + $outExe) -ForegroundColor Green
Write-Host (' 体积：' + $exeMB + ' MB（内嵌 payload ' + $zipMB + ' MB）') -ForegroundColor Green
if ($runtimeOk) { Write-Host ' Python 运行时：已预置（安装后零下载启动）' -ForegroundColor Green }
else { Write-Host ' Python 运行时：uv.exe 兜底（首次启动需联网）' -ForegroundColor Yellow }
if (-not $SkipUpstream) { Write-Host ' 上游网关：wb2api.exe 已打包' -ForegroundColor Green }
Write-Host '==================================' -ForegroundColor Green
