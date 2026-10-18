param([switch]$NoBrowser, [switch]$TaskMode)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$configPath = Join-Path $projectRoot 'alphaagent.local.json'
$localConfig = if (Test-Path -LiteralPath $configPath) { Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json } else { [pscustomobject]@{ port = 8839 } }
$servicePort = $localConfig.port
$serviceUrl = "http://127.0.0.1:$servicePort"
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$dataLocation = if ($localConfig.data) { $localConfig.data } else { '.phase3-quant-o/alpha-history-real-02' }
$expectedData = if ([System.IO.Path]::IsPathRooted($dataLocation)) { [System.IO.Path]::GetFullPath($dataLocation) } else { [System.IO.Path]::GetFullPath((Join-Path $projectRoot $dataLocation)) }
$expectedInstance = & $pythonPath -c 'import sys; from marketpulse.quant.contracts import digest; print(digest(sys.argv[1]))' $expectedData
$health = $null
try { $health = Invoke-RestMethod "$serviceUrl/api/health" -TimeoutSec 3 } catch { }
if ($health) {
    $daily = $null
    try { $daily = Invoke-RestMethod "$serviceUrl/api/quant/daily/status" -TimeoutSec 3 } catch { }
    if (-not $daily.enabled) { throw "此端口运行的是旧启动方式。请先停止旧服务，再运行本脚本；不要同时启动两个实例。" }
    if ($daily.instance_id -ne $expectedInstance) { throw '此端口运行的是另一数据目录，请核对 alphaagent.local.json；未另建空库或覆盖历史。' }
} else {
    $pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $pythonPath)) { throw '未找到项目 Python 环境。' }
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'frontend\dist\index.html'))) { throw '请先在 frontend 运行 npm run build。' }
    $logRoot = Join-Path $projectRoot 'logs'
    New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
    $startedProcess = Start-Process -FilePath $pythonPath -ArgumentList @('-u', 'scripts/serve_alphaagent.py') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logRoot 'alphaagent.stdout.log') -RedirectStandardError (Join-Path $logRoot 'alphaagent.stderr.log') -PassThru
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Seconds 1
        try { $health = Invoke-RestMethod "$serviceUrl/api/health" -TimeoutSec 2; break } catch { }
    }
    if (-not $health) { throw '启动未完成，请查看 logs/alphaagent.stderr.log。' }
}
Write-Output "AlphaAgent: $serviceUrl/#/"
if (-not $NoBrowser) { Start-Process "$serviceUrl/#/" }

# A scheduled action owns and monitors only the process it started.
if ($TaskMode -and $startedProcess) {
    $startedProcess.WaitForExit()
    exit $startedProcess.ExitCode
}
