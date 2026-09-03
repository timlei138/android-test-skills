#!/usr/bin/env pwsh
# Android 测试台 Web 界面 启动/停止/状态（Windows 版）
#
# 用法:
#   pwsh -File webui.ps1 start
#   pwsh -File webui.ps1 start -Port 9000
#   pwsh -File webui.ps1 stop
#   pwsh -File webui.ps1 status
#
# 等价于 macOS/Linux 的 ./webui.sh。数据目录（SQLite + 知识库）由
# DSH_ANDROID_TEST_DIR 决定，默认 ~/dsh-android-test，保证从任何位置启动
# 都读写同一个库与知识库。

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('start', 'stop', 'status', 'restart')]
    [string]$Action = 'start',

    [int]$Port = 8900,

    [string]$Workspace = $(if ($env:DSH_ANDROID_TEST_DIR) { $env:DSH_ANDROID_TEST_DIR }
                           else { Join-Path $HOME 'dsh-android-test' })
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$SkillDir  = $PSScriptRoot
$WebUI     = Join-Path $SkillDir 'framework\webui.py'
$VenvPy    = if ($env:DSH_ANDROID_TEST_VENV) { Join-Path $env:DSH_ANDROID_TEST_VENV 'Scripts\python.exe' }
             else { Join-Path $Workspace '.venv\Scripts\python.exe' }
$PidFile   = Join-Path $SkillDir '.webui.pid'
$LogFile   = Join-Path $SkillDir 'webui.log'

# --------------------------------------------------------- 进程状态辅助
# 用 CIM 查询确认 PID 存活且命令行确实是我们的 webui.py，避免 PID 复用误判。
function Get-WebUiProcess {
    if (-not (Test-Path -LiteralPath $PidFile)) { return $null }
    $raw = (Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if (-not $raw) { return $null }
    $raw = $raw.Trim()
    $id = 0
    if (-not [int]::TryParse($raw, [ref]$id)) { return $null }

    $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $id" -ErrorAction SilentlyContinue
    if (-not $proc) { return $null }
    if ($proc.CommandLine -notmatch 'webui\.py') { return $null }
    return $proc
}

function Show-Status {
    param($Proc)
    if ($Proc) {
        Write-Host "✅ 运行中: http://127.0.0.1:$Port (PID $($Proc.ProcessId))" -ForegroundColor Green
        Write-Host "   工作区: $Workspace" -ForegroundColor DarkGray
        return $true
    }
    Write-Host '❌ 未运行' -ForegroundColor DarkGray
    return $false
}

# --------------------------------------------------------------- 动作分发
switch ($Action) {

    'status' {
        $p = Get-WebUiProcess
        if (-not (Show-Status $p)) {
            Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
        }
    }

    'stop' {
        $p = Get-WebUiProcess
        if (-not $p) {
            Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
            Write-Host 'ℹ️  未在运行' -ForegroundColor DarkGray
            exit 0
        }
        $id = $p.ProcessId
        # 先停进程本身；Stop-Process 带 -Force 会连带子进程树
        Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 600
        $still = Get-CimInstance Win32_Process -Filter "ProcessId = $id" -ErrorAction SilentlyContinue
        if ($still) {
            & taskkill.exe /PID $id /T /F 2>&1 | Out-Null
            Start-Sleep -Milliseconds 400
        }
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
        Write-Host '🛑 已停止' -ForegroundColor Yellow
    }

    'restart' {
        & $PSCommandPath stop -Workspace $Workspace | Out-Host
        Start-Sleep -Seconds 1
        & $PSCommandPath start -Port $Port -Workspace $Workspace | Out-Host
    }

    'start' {
        if (-not (Test-Path -LiteralPath $VenvPy)) {
            Write-Host '❌ 未找到 venv Python，请先运行环境安装:' -ForegroundColor Red
            Write-Host "   pwsh -File `"$SkillDir\setup.ps1`"" -ForegroundColor Yellow
            Write-Host "   期望的 venv: $VenvPy" -ForegroundColor DarkGray
            exit 1
        }
        if (-not (Test-Path -LiteralPath $WebUI)) {
            Write-Host "❌ 未找到 webui.py: $WebUI" -ForegroundColor Red
            exit 1
        }

        $existing = Get-WebUiProcess
        if ($existing) {
            Write-Host "⚠️  已在运行 (PID $($existing.ProcessId))" -ForegroundColor Yellow
            Show-Status $existing | Out-Null
            exit 0
        }
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue

        # 显式指定数据目录与 skill 包位置，保证从任何位置启动都读写同一份资产
        $env:DSH_ANDROID_TEST_DIR = $Workspace
        $env:DSH_SKILL_DIR = $SkillDir
        # webui.py 会打印 emoji（📊）等 BMP 外字符，Windows 默认 GBK stdout
        # 无法编码会直接 UnicodeEncodeError 退出，故强制 UTF-8 输出。
        $env:PYTHONIOENCODING = 'utf-8'
        $env:PYTHONUTF8 = '1'

        $proc = Start-Process -FilePath $VenvPy `
                              -ArgumentList @("`"$WebUI`"", '--port', $Port) `
                              -WorkingDirectory (Join-Path $SkillDir 'framework') `
                              -RedirectStandardOutput $LogFile `
                              -RedirectStandardError (Join-Path $SkillDir 'webui.err.log') `
                              -PassThru -WindowStyle Hidden

        Set-Content -LiteralPath $PidFile -Value $proc.Id -Encoding ASCII
        Start-Sleep -Seconds 2

        $alive = Get-WebUiProcess
        if (-not $alive) {
            Write-Host '❌ 启动后立即退出，请查看日志:' -ForegroundColor Red
            Write-Host "   $LogFile" -ForegroundColor DarkGray
            Write-Host "   $(Join-Path $SkillDir 'webui.err.log')" -ForegroundColor DarkGray
            Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
            exit 1
        }

        Write-Host '✅ 已启动' -ForegroundColor Green
        Write-Host "   URL:    http://127.0.0.1:$Port" -ForegroundColor Cyan
        Write-Host "   工作区: $Workspace" -ForegroundColor DarkGray
        Write-Host "   日志:   $LogFile" -ForegroundColor DarkGray
        Write-Host "   PID:    $($alive.ProcessId)" -ForegroundColor DarkGray
    }
}
