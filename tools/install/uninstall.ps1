param([switch]$KeepData)

$ErrorActionPreference = "Stop"

$InstallDir = Join-Path $env:USERPROFILE ".kz-eng-mp\agent-otel\redaction-proxy"
function Info($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }

if (Get-ScheduledTask -TaskName "AgentOtel.RedactionProxy" -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName "AgentOtel.RedactionProxy" -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName "AgentOtel.RedactionProxy" -Confirm:$false
    Info "removed Scheduled Task"
}

if (-not $KeepData.IsPresent -and (Test-Path $InstallDir)) {
    Remove-Item -Recurse -Force $InstallDir
    Info "removed $InstallDir"
}

$Settings = Join-Path $env:USERPROFILE ".claude\settings.json"
if (Test-Path $Settings) {
    Copy-Item $Settings "$Settings.bak-$(Get-Date -UFormat %s)"
    $obj = Get-Content -Raw $Settings | ConvertFrom-Json
    if ($obj.env) {
        $obj.env.OTEL_EXPORTER_OTLP_ENDPOINT = "https://agent-otel-collector.kindbay-ee480b05.centralus.azurecontainerapps.io"
        $obj | ConvertTo-Json -Depth 100 | Set-Content -Path $Settings
        Info "restored cloud endpoint in $Settings"
    }
}

Info "Done. To re-install: tools\install\install.ps1"
