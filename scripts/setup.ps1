#!/usr/bin/env pwsh
# android-test-skills skill 一键环境安装（Windows 版）
#
# 用法:
#   pwsh -File setup.ps1
#   pwsh -File setup.ps1 -WithAgent                       # 额外装 AutoGLM agent 环境
#   pwsh -File setup.ps1 -Workspace D:\android-test-skills-data   # 指定工作区
#   pwsh -File setup.ps1 -Python "C:\Program Files\python\3.11\python.exe"
#   pwsh -File setup.ps1 -SkipDeviceCheck                 # 没插设备时装依赖
#   pwsh -File setup.ps1 -Recreate                        # 重建已存在的 venv
#
# 等价于 macOS/Linux 的 bash setup.sh，产出同样的目录布局：
#   <Workspace>\.venv\Scripts\python.exe
#   <Workspace>\framework\
#   <skill包>\cases\     用例（随版本同步）

[CmdletBinding()]
param(
    [string]$Workspace = (Join-Path $HOME 'android-test-skills-data'),
    [string]$Python = $env:PYTHON,
    [switch]$WithAgent,
    [switch]$SkipDeviceCheck,
    [switch]$Recreate,
    [switch]$Smoke
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$SkillDir = Split-Path -Parent $PSScriptRoot
$VenvDir  = Join-Path $Workspace '.venv'
$VenvPy   = Join-Path $VenvDir 'Scripts\python.exe'

# ---------------------------------------------------------------- 输出辅助
function Write-Step { param([string]$Text) Write-Host "▶ $Text" -ForegroundColor Cyan }
function Write-Ok   { param([string]$Text) Write-Host "  ✅ $Text" -ForegroundColor Green }
function Write-Warn { param([string]$Text) Write-Host "  ⚠️  $Text" -ForegroundColor Yellow }
function Write-Fail { param([string]$Text) Write-Host "  ❌ $Text" -ForegroundColor Red }
function Write-Info { param([string]$Text) Write-Host "     $Text" -ForegroundColor DarkGray }

# 原生命令（python/pip/u2）常把进度、DEBUG 日志写进 stderr，在
# $ErrorActionPreference='Stop' 下会被 PowerShell 当作 NativeCommandError
# 直接终止脚本。这里统一合并 stderr 进 stdout，仅依据退出码判定成败。
function Invoke-Native {
    param(
        [Parameter(Mandatory)][string]$Exe,
        [string[]]$Arguments = @(),
        [int[]]$AllowExit = @(0)
    )
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = @(& $Exe @Arguments 2>&1 | ForEach-Object { $_.ToString() })
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
    return @{ Output = $output; ExitCode = $code; Ok = ($AllowExit -contains $code) }
}

# ------------------------------------------------------- Python 探测/校验
# 返回 @{ Path; Version } 或 $null。Windows 商店占位版 python 不会通过校验。
function Test-PythonPath {
    param([string]$Path)
    if (-not $Path) { return $null }
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    $out = $null
    try {
        $out = & $Path -c "import sys;print('%d.%d.%d'%sys.version_info[:3])" 2>$null
    } catch { return $null }
    if (-not $out) { return $null }
    $line = ([string]$out).Trim()
    if ($line -notmatch '^\d+\.\d+\.\d+$') { return $null }
    $v = [version]$line
    if ($v -lt [version]'3.9.0') { return $null }
    return @{ Path = $Path; Version = $v }
}

function Resolve-Python {
    param([string]$Explicit)

    $paths = New-Object System.Collections.ArrayList
    if ($Explicit) { [void]$paths.Add($Explicit) }

    foreach ($name in @('python', 'python3', 'py')) {
        $cmd = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue |
               Select-Object -First 1
        if ($cmd) { [void]$paths.Add($cmd.Source) }
    }

    # 已知安装目录兜底（绕过 PATH 里可能存在的商店占位别名）
    foreach ($root in @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python'),
        'C:\Program Files\python',
        'C:\Python313', 'C:\Python312', 'C:\Python311', 'C:\Python310'
    )) {
        if (Test-Path -LiteralPath $root) {
            Get-ChildItem -LiteralPath $root -Filter 'python.exe' -Recurse -Depth 2 -ErrorAction SilentlyContinue |
                ForEach-Object { [void]$paths.Add($_.FullName) }
        }
    }

    foreach ($p in $paths) {
        $hit = Test-PythonPath $p
        if ($hit) { return $hit }
    }
    return $null
}

# ------------------------------------------------------------------- 开场
Write-Host ''
Write-Host '════════════════════════════════════════════' -ForegroundColor DarkCyan
Write-Host '  Android GUI 测试环境安装 (Windows)' -ForegroundColor Cyan
Write-Host "  工作目录: $Workspace" -ForegroundColor Cyan
Write-Host '════════════════════════════════════════════' -ForegroundColor DarkCyan
Write-Host ''

# ------------------------------------------------------------- 1. adb 检查
Write-Step '1/5 检查 adb...'
$adb = Get-Command adb -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $adb) {
    Write-Fail '未找到 adb，请安装 Android SDK platform-tools 并加入 PATH'
    Write-Info '下载: https://developer.android.com/tools/releases/platform-tools'
    exit 1
}
$adbPath = $adb.Source
try { $adbVer = (& $adbPath version 2>$null | Select-Object -First 1) } catch { $adbVer = $null }
Write-Ok ($(if ($adbVer) { $adbVer } else { $adbPath }))

# ------------------------------------------------------------ 2. 设备检查
Write-Step '2/5 检查设备...'
if ($SkipDeviceCheck) {
    Write-Warn '已跳过（-SkipDeviceCheck），装完请自行确认 adb devices 有授权设备'
} else {
    $deviceLines = @()
    try {
        $deviceLines = @(& $adbPath devices 2>$null | Where-Object { $_.Trim() -match '\bdevice\s*$' })
    } catch { }
    if ($deviceLines.Count -eq 0) {
        Write-Fail '未检测到已授权设备，请连接并开启 USB 调试'
        Write-Info '没插设备也要装依赖的话: pwsh -File setup.ps1 -SkipDeviceCheck'
        exit 1
    }
    & $adbPath devices -l | Where-Object { $_.Trim() } | ForEach-Object { Write-Info $_ }
    Write-Ok "设备已连接 ($($deviceLines.Count) 台)"
}

# -------------------------------------------------------- 3. venv 与依赖
Write-Step '3/5 创建虚拟环境并安装依赖...'

$py = Resolve-Python $Python
if (-not $py) {
    Write-Fail '未找到可用的 Python 3.9+'
    Write-Info '安装: https://www.python.org/downloads/ （勾选 Add python.exe to PATH）'
    Write-Info '或显式指定: pwsh -File setup.ps1 -Python "C:\path\to\python.exe"'
    exit 1
}
Write-Info "使用 Python $($py.Version) — $($py.Path)"

if ((Test-Path -LiteralPath $VenvPy) -and $Recreate) {
    Write-Info '检测到已有 venv 且指定 -Recreate，删除重建...'
    Remove-Item -LiteralPath $VenvDir -Recurse -Force
}

if (-not (Test-Path -LiteralPath $VenvPy)) {
    New-Item -ItemType Directory -Path $Workspace -Force | Out-Null
    Write-Info '创建 venv（首次约需几十秒）...'
    try {
        $r = Invoke-Native $py.Path @('-m', 'venv', $VenvDir)
        if (-not $r.Ok) { throw "venv 退出码 $($r.ExitCode)" }
    } catch {
        Write-Fail "创建 venv 失败: $($_.Exception.Message)"
        exit 1
    }
} else {
    Write-Info '复用已有 venv'
}
Write-Ok "venv 就绪 ($VenvPy)"

Write-Info '升级 pip / setuptools / wheel ...'
$r = Invoke-Native $VenvPy @('-m', 'pip', 'install', '--upgrade', 'pip', 'setuptools', 'wheel')
$r.Output | Select-Object -Last 3 | ForEach-Object { Write-Info $_ }

Write-Info '安装 uiautomator2 / rapidocr_onnxruntime（首次较慢，请耐心）...'
$reqFile = Join-Path $SkillDir 'requirements.txt'
$r = Invoke-Native $VenvPy @('-m', 'pip', 'install', '-r', $reqFile)
$r.Output | Select-Object -Last 5 | ForEach-Object { Write-Info $_ }
if (-not $r.Ok) {
    Write-Fail "依赖安装失败（退出码 $($r.ExitCode)），请检查网络或代理设置后重试"
    exit 1
}
Write-Ok '依赖安装完成'

# import 自检：装上了但 import 不了的隐性失败在这里暴露（与 setup.sh 对齐）
Write-Info '依赖 import 自检 ...'
$r = Invoke-Native $VenvPy @('-c', 'import uiautomator2, rapidocr_onnxruntime')
if (-not $r.Ok) {
    Write-Fail '依赖 import 自检失败（包装上了但无法导入）'
    exit 1
}

# ------------------------------------------------- 4. uiautomator2 初始化
Write-Step '4/5 初始化 uiautomator2 设备端...'
if ($SkipDeviceCheck) {
    Write-Warn '跳过（未检查设备）'
} else {
    Write-Info '首次会在手机上安装 atx-agent / uiautomator apk，请留意设备授权提示...'
    $r = Invoke-Native $VenvPy @('-m', 'uiautomator2', 'init')
    $r.Output | Select-Object -Last 5 | ForEach-Object { Write-Info $_ }
    if (-not $r.Ok) {
        Write-Warn "u2 init 退出码 $($r.ExitCode)（可稍后手动重跑）"
    } else {
        Write-Ok 'u2 初始化完成'
    }
}

# ------------------------------------------------------ 5. 建工作区数据目录 + 首次复制 cases/knowledge
Write-Step '5/5 初始化工作区...'
New-Item -ItemType Directory -Path $Workspace -Force | Out-Null
# 运行产物
foreach ($dir in @('storage', 'storage\reports', 'storage\screenshots', 'storage\logs')) {
    $dst = Join-Path $Workspace $dir
    New-Item -ItemType Directory -Path $dst -Force | Out-Null
    Write-Ok "$dir -> $dst"
}
# 首次复制 cases/ + knowledge/（仅工作区不存在时复制，后续修改只动工作区）
$casesSrc = Join-Path $SkillDir 'cases'
$casesDst = Join-Path $Workspace 'cases'
if (-not (Test-Path -LiteralPath $casesDst)) {
    Copy-Item -LiteralPath $casesSrc -Destination $casesDst -Recurse -Force
    Write-Ok "首次复制 cases/ → $casesDst"
} else {
    Write-Info "cases/ 已存在，跳过（后续修改只动工作区副本）"
}
$kbSrc = Join-Path $SkillDir 'knowledge'
$kbDst = Join-Path $Workspace 'knowledge'
if (-not (Test-Path -LiteralPath $kbDst)) {
    Copy-Item -LiteralPath $kbSrc -Destination $kbDst -Recurse -Force
    Write-Ok "首次复制 knowledge/ → $kbDst"
} else {
    Write-Info "knowledge/ 已存在，跳过"
}

# ------------------------------------------------- 可选: AutoGLM agent 环境
if ($WithAgent) {
    Write-Step '可选 安装 AutoGLM agent 环境（Python 3.10+）...'
    $agentPy = $null
    $py313 = Get-Command python3.13 -CommandType Application -ErrorAction SilentlyContinue |
             Select-Object -First 1
    if ($py313) {
        $agentPy = $py313.Source
    } else {
        $probe = Get-ChildItem -LiteralPath (Join-Path $env:LOCALAPPDATA 'Programs\Python') `
                               -Filter 'python.exe' -Recurse -Depth 2 -ErrorAction SilentlyContinue
        if ($probe) {
            $match = $probe | Where-Object { $_.FullName -match 'Python3(1[0-9])' } | Select-Object -First 1
            if ($match) { $agentPy = $match.FullName }
        }
    }
    if (-not $agentPy -and $py.Version -ge [version]'3.10.0') { $agentPy = $py.Path }

    if ($agentPy) {
        $venv313 = Join-Path $Workspace '.venv313'
        $v313py  = Join-Path $venv313 'Scripts\python.exe'
        if (-not (Test-Path -LiteralPath $v313py)) {
            & $agentPy -m venv $venv313
        }
        $autoglm = $env:AUTOGLM_DIR
        if ($autoglm -and (Test-Path -LiteralPath $autoglm)) {
            (Invoke-Native $v313py @('-m', 'pip', 'install', '-e', $autoglm)).Output |
                Select-Object -Last 3 | ForEach-Object { Write-Info $_ }
        } else {
            (Invoke-Native $v313py @('-m', 'pip', 'install', 'openai', 'rapidocr_onnxruntime')).Output |
                Select-Object -Last 3 | ForEach-Object { Write-Info $_ }
            Write-Info '未设 AUTOGLM_DIR，改为安装 openai 等基础依赖'
        }
        Write-Ok "agent 环境就绪 ($v313py)"
    } else {
        Write-Warn '未找到 Python 3.10+，跳过 agent 环境（不影响基础测试）'
    }
}

# ------------------------------------------------------------------- 收尾
Write-Host ''
Write-Host '════════════════════════════════════════════' -ForegroundColor DarkCyan
Write-Host '✅ 安装完成！快速开始:' -ForegroundColor Green
Write-Host ''
Write-Host "  # 跑示例用例（用例按包名分目录：cases\<包名>\<编号>.py）" -ForegroundColor DarkGray
Write-Host "  pwsh -File `"$SkillDir\scripts\run_case.ps1`" -Case `"com.zui.calendar/172.py`""
Write-Host ''
Write-Host "  # 等价的原生写法（注意 cd 到 skill 包，不是工作区）"
Write-Host "  cd `"$SkillDir\framework`""
Write-Host "  & `"$VenvPy`" run_case.py `"com.zui.calendar/172.py`""
Write-Host ''
Write-Host "  # 启动 Web 测试台 (http://127.0.0.1:8900)"
Write-Host "  pwsh -File `"$SkillDir\scripts\webui.ps1`" start"
Write-Host ''
Write-Host "  工作区: $Workspace" -ForegroundColor DarkGray
Write-Host "  报告输出: $Workspace\storage\reports\" -ForegroundColor DarkGray

if ($Smoke) {
    Write-Host ''
    Write-Host "▶ 执行 smoke 探针..." -ForegroundColor Cyan
    & $VenvPy (Join-Path $SkillDir 'framework\smoke.py')
    $smokeCode = $LASTEXITCODE
    if ($smokeCode -eq 0) {
        Write-Ok 'smoke 全链路通畅'
    } else {
        Write-Warn "smoke 退出码 $smokeCode（设备/adb 问题，请检查连接）"
    }
} else {
    Write-Host ''
    Write-Host "  建议执行 smoke 探针验证全链路:" -ForegroundColor DarkGray
    Write-Host "  & `"$VenvPy`" `"$SkillDir\framework\smoke.py`"" -ForegroundColor DarkGray
}

Write-Host '════════════════════════════════════════════' -ForegroundColor DarkCyan
