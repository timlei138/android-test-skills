#!/usr/bin/env pwsh
# 用例执行器（Windows 版）
#
# 用法（用例按被测 App 包名分目录：cases\<包名>\<编号>.py）:
#   pwsh -File run_case.ps1 -Case "com.zui.calendar/172.py"  # 带子目录路径
#   pwsh -File run_case.ps1 -Case "172.py"                   # 裸文件名（递归查找）
#   pwsh -File run_case.ps1 -Case "D:\mycase.py"             # 绝对路径直接用
#   pwsh -File run_case.ps1 -List                            # 只列出可用用例
#   pwsh -File run_case.ps1 -Case "172"                      # 模糊匹配，自动补 .py
#
# 等价于 macOS/Linux 的:
#   cd ~/dsh-android-test/framework && .venv/bin/python run_case.py <用例>
#
# 报告输出: <Workspace>\storage\reports\<用例名>_报告.md

[CmdletBinding()]
param(
    [string]$Case,
    [string]$Workspace = $(if ($env:DSH_ANDROID_TEST_DIR) { $env:DSH_ANDROID_TEST_DIR }
                           else { Join-Path $HOME 'dsh-android-test' }),
    [string]$Python,
    [switch]$List
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$SkillDir = $PSScriptRoot
$VenvPy = if ($Python) { $Python } else { Join-Path $Workspace '.venv\Scripts\python.exe' }

# skill 包内的 framework 始终可用（无需先跑 setup.sh 拷贝）
$FrameworkDirs = @(
    (Join-Path $Workspace 'framework'),
    (Join-Path $SkillDir 'framework')
)

# ------------------------------------------------------------- 前置检查
if (-not (Test-Path -LiteralPath $VenvPy)) {
    Write-Host '❌ 未找到 venv Python，请先运行环境安装:' -ForegroundColor Red
    Write-Host "   pwsh -File `"$SkillDir\setup.ps1`"" -ForegroundColor Yellow
    Write-Host "   期望的 venv: $VenvPy" -ForegroundColor DarkGray
    exit 1
}

$runner = $null
$fwDir  = $null
foreach ($d in $FrameworkDirs) {
    $candidate = Join-Path $d 'run_case.py'
    if (Test-Path -LiteralPath $candidate) { $runner = $candidate; $fwDir = $d; break }
}
if (-not $runner) {
    Write-Host '❌ 未找到 framework\run_case.py' -ForegroundColor Red
    $FrameworkDirs | ForEach-Object { Write-Host "   已查找: $_" -ForegroundColor DarkGray }
    exit 1
}

# --------------------------------------------------------------- 列用例
# 用例单一数据源 = <skill包>\cases（随版本同步，团队共享）；fwDir 为旧布局兼容
$caseDirs = @(
    (Join-Path $SkillDir 'cases'),
    (Join-Path $fwDir 'cases')
) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -Unique

if ($List -or -not $Case) {
    Write-Host '可用用例（按包名分目录）:' -ForegroundColor Cyan
    foreach ($d in $caseDirs) {
        Write-Host "  [$d]" -ForegroundColor DarkGray
        Get-ChildItem -LiteralPath $d -Filter '*.py' -File -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -notlike '_*' -and $_.FullName -notmatch '__pycache__' } |
            ForEach-Object {
                $rel = $_.FullName.Substring($d.Length).TrimStart('\', '/') -replace '\\', '/'
                Write-Host "    - $rel"
            }
    }
    Write-Host ''
    Write-Host '用法: pwsh -File run_case.ps1 -Case "<包名目录>/<用例名>.py"' -ForegroundColor DarkGray
    exit 0
}

# --------------------------------------------------------------- 解析用例
function Resolve-CasePath {
    param([string]$Name)

    if ([System.IO.Path]::IsPathRooted($Name)) {
        return $(if (Test-Path -LiteralPath $Name) { $Name } else { $null })
    }
    $n = $Name
    if (-not $n.EndsWith('.py', [System.StringComparison]::OrdinalIgnoreCase)) { $n = "$n.py" }

    # 带子目录的相对路径（com.zui.calendar/172.py，正反斜杠均可）
    if ($n -match '[/\\]') {
        $rel = $n -replace '/', '\'
        foreach ($d in $caseDirs) {
            $p = Join-Path $d $rel
            if (Test-Path -LiteralPath $p) { return $p }
        }
        return $null
    }

    # 裸文件名：递归精确匹配
    $hits = @()
    foreach ($d in $caseDirs) {
        $hits += @(Get-ChildItem -LiteralPath $d -Filter $n -File -Recurse -ErrorAction SilentlyContinue |
                   Where-Object { $_.Name -notlike '_*' -and $_.FullName -notmatch '__pycache__' })
    }
    if ($hits.Count -eq 1) { return $hits[0].FullName }
    if ($hits.Count -gt 1) {
        Write-Host "❌ 用例名 '$Name' 匹配到多个文件，请写带子目录的路径:" -ForegroundColor Red
        $hits | ForEach-Object { Write-Host "    - $($_.FullName)" -ForegroundColor DarkGray }
        return ':AMBIGUOUS:'
    }
    # 模糊匹配（如输入 "172" 命中 "com.zui.calendar/172.py"）
    $hits = @()
    foreach ($d in $caseDirs) {
        $hits += @(Get-ChildItem -LiteralPath $d -Filter "*$n*" -File -Recurse -ErrorAction SilentlyContinue |
                   Where-Object { $_.Name -notlike '_*' -and $_.FullName -notmatch '__pycache__' })
    }
    if ($hits.Count -eq 1) { return $hits[0].FullName }
    if ($hits.Count -gt 1) {
        Write-Host "❌ 用例名 '$Name' 模糊匹配到多个文件，请写更完整的名字:" -ForegroundColor Red
        $hits | ForEach-Object { Write-Host "    - $($_.FullName)" -ForegroundColor DarkGray }
        return ':AMBIGUOUS:'
    }
    return $null
}

$casePath = Resolve-CasePath $Case
if ($casePath -eq ':AMBIGUOUS:') { exit 1 }
if (-not $casePath) {
    Write-Host "❌ 用例文件不存在: $Case" -ForegroundColor Red
    foreach ($d in $caseDirs) { Write-Host "   已查找: $d" -ForegroundColor DarkGray }
    Write-Host ''
    Write-Host '列出可用用例: pwsh -File run_case.ps1 -List' -ForegroundColor DarkGray
    exit 1
}

# ---------------------------------------------------------------- 启动 Web UI
$webuiScript = Join-Path $SkillDir 'webui.ps1'
if (Test-Path -LiteralPath $webuiScript) {
    try {
        $statusOutput = & pwsh -NoProfile -ExecutionPolicy Bypass -File $webuiScript status 2>&1 | Out-String
        $webuiRunning = $statusOutput -match '运行中'
    } catch { $webuiRunning = $false }
    if (-not $webuiRunning) {
        Write-Host '🚀 正在后台启动 Web UI...' -ForegroundColor Cyan
        Start-Process -FilePath powershell -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',$webuiScript,'start') -WindowStyle Hidden -WorkingDirectory $SkillDir
        Start-Sleep -Seconds 3
    }
}

# ----------------------------------------------------------------- 执行
Write-Host '════════════════════════════════════════════' -ForegroundColor DarkCyan
Write-Host '  执行用例' -ForegroundColor Cyan
Write-Host "  用例: $casePath" -ForegroundColor DarkGray
Write-Host "  解释器: $VenvPy" -ForegroundColor DarkGray
Write-Host '════════════════════════════════════════════' -ForegroundColor DarkCyan
Write-Host ''

Push-Location $fwDir
try {
    # 用例与框架会打印 emoji（🎉/✅/❌）等字符，Windows 默认 GBK stdout
    # 无法编码会抛 UnicodeEncodeError，故强制 UTF-8 输出。
    $prevUtf8 = $env:PYTHONUTF8
    $prevIo   = $env:PYTHONIOENCODING
    $prevSkill = $env:DSH_SKILL_DIR
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    # 显式声明 skill 包位置：工作区 framework 副本据此定位 cases/knowledge
    $env:DSH_SKILL_DIR = $SkillDir
    try {
        & $VenvPy $runner $casePath
        $code = $LASTEXITCODE
    } finally {
        $env:PYTHONUTF8 = $prevUtf8
        $env:PYTHONIOENCODING = $prevIo
        $env:DSH_SKILL_DIR = $prevSkill
    }
}
finally {
    Pop-Location
}

Write-Host ''
if ($code -eq 0) {
    $reportDir = Join-Path $Workspace 'storage\reports'
    if (Test-Path -LiteralPath $reportDir) {
        $latest = Get-ChildItem -LiteralPath $reportDir -Filter '*.md' -File -ErrorAction SilentlyContinue |
                  Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if ($latest) {
            Write-Host "📄 最新报告: $($latest.FullName)" -ForegroundColor Green
        }
    }
} else {
    $meaning = @{ 1 = 'FAIL（断言失败）'; 2 = 'BLOCKED（环境/前置不满足）'; 3 = 'ERROR（脚本/框架/设备异常）' }[$code]
    Write-Host "⚠️  用例执行退出码 $code$(if ($meaning) { "：$meaning" })" -ForegroundColor Yellow
}
exit $code
