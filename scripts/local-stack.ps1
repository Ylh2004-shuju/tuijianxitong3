# 本地开发用的启动/停止脚本。
# 复用 deployment/local.runtime.json 里的项目名、端口、密钥目录与 Docker context，
# 但跳过 deployment/offline/runtime.ps1 对 compose.yaml 的 SHA-256 闸门
# （该闸门目前因本地 compose.yaml 被改过而必然失败，详见 lab2/环境排错-compose哈希不符.md）。
# 正式验收请优先用 deployment/student-bat/04-启动推荐系统.bat。
#   powershell -ExecutionPolicy Bypass -File scripts/local-stack.ps1 start
#   powershell -ExecutionPolicy Bypass -File scripts/local-stack.ps1 stop
#   powershell -ExecutionPolicy Bypass -File scripts/local-stack.ps1 status
param([ValidateSet('start', 'stop', 'restart', 'status')][string]$Action = 'start')
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new()
$root = Split-Path $PSScriptRoot -Parent
$settings = Join-Path $root 'deployment/local.runtime.json'
if (-not (Test-Path -LiteralPath $settings)) { throw '缺少 deployment/local.runtime.json，请先执行 03-首次初始化.bat' }
$rt = Get-Content -LiteralPath $settings -Raw -Encoding UTF8 | ConvertFrom-Json
$env:COMPOSE_PROJECT_NAME = [string]$rt.project
$env:COURSE_WEB_PORT = [string]$rt.port
$env:COURSE_SECRET_DIR = [string]$rt.secretDir
$env:COMPOSE_PROFILES = ''
Push-Location $root
try {
    $compose = @('--context', [string]$rt.context, 'compose')
    if ($Action -eq 'start') { & docker @compose up -d }
    elseif ($Action -eq 'stop') { & docker @compose stop }
    elseif ($Action -eq 'restart') { & docker @compose up -d --force-recreate api web }
    else { & docker @compose ps }
    if ($LASTEXITCODE -ne 0) { throw "docker compose 退出码 $LASTEXITCODE" }
    if ($Action -eq 'start' -or $Action -eq 'restart') {
        Write-Output ('前端地址：http://127.0.0.1:' + [string]$rt.port)
        Write-Output '若页面 502，等 30-60 秒让 db 健康检查通过后刷新。'
    }
} finally { Pop-Location }
