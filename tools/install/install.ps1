# Install the agent-otel redaction proxy as a per-user Scheduled Task
# on Windows. Mirror of install.sh for macOS/Linux.
#
# Run from a regular PowerShell (no admin needed). Optionally pass
# -EnablePII to enable the privacy-filter ML model (downloads ~400 MB
# of ONNX weights and adds ~250 ms / record + ~2.4 GB RSS).

param(
    [switch]$EnablePII,
    [switch]$Yes  # non-interactive
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$ProxySrc    = Join-Path $RepoRoot "tools\redaction-proxy.py"
$PatternsSrc = Join-Path $RepoRoot "infra\generate-collector-config.py"
$LauncherSrc = Join-Path $PSScriptRoot "launcher.ps1"
$TaskTpl     = Join-Path $PSScriptRoot "services\agent-otel-redaction-proxy.task.xml.tmpl"

$InstallRoot = Join-Path $env:USERPROFILE ".kz-eng-mp\agent-otel"
$InstallDir  = Join-Path $InstallRoot "redaction-proxy"
$LogDir      = Join-Path $InstallDir "logs"

function Info($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }

# ── prompt for PII redaction ───────────────────────────────────────
$PII = $EnablePII.IsPresent
if (-not $PII -and -not $Yes.IsPresent) {
    Write-Host ""
    Write-Host "Optional: enable PII redaction via openai/privacy-filter ML model."
    Write-Host "  - Adds ~250 ms latency per OTLP record."
    Write-Host "  - Adds ~2.4 GB RSS to the proxy."
    Write-Host "  - Downloads ~400 MB of ONNX weights to %USERPROFILE%\.cache\huggingface."
    Write-Host ""
    $resp = Read-Host "Enable PII redaction? [y/N]"
    if ($resp -match '^[yY]') { $PII = $true }
}

Info "platform: windows   pii: $(if ($PII) { 'enabled' } else { 'disabled' })"
Info "install dir: $InstallDir"

# ── stage files ────────────────────────────────────────────────────
New-Item -ItemType Directory -Force -Path $InstallDir, $LogDir | Out-Null
Copy-Item -Force $ProxySrc    (Join-Path $InstallDir "redaction-proxy.py")
Copy-Item -Force $PatternsSrc (Join-Path $InstallDir "generate-collector-config.py")
Copy-Item -Force $LauncherSrc (Join-Path $InstallDir "launcher.ps1")

# ── venv + deps ────────────────────────────────────────────────────
$VenvPython = Join-Path $InstallDir "venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Info "creating venv"
    python -m venv (Join-Path $InstallDir "venv")
}
Info "installing proxy deps into venv"
& $VenvPython -m pip install --quiet --upgrade pip
& $VenvPython -m pip install --quiet `
    fastapi 'uvicorn[standard]' httpx opentelemetry-proto google-re2

if ($PII) {
    Info "installing privacy-filter deps"
    & $VenvPython -m pip install --quiet onnxruntime tokenizers numpy huggingface_hub
    Info "downloading openai/privacy-filter (q4f16, ~400 MB)"
    & $VenvPython -c "from huggingface_hub import snapshot_download; snapshot_download('openai/privacy-filter', allow_patterns=['onnx/model_q4f16.onnx', 'tokenizer.json', 'config.json']); print('done')"
}

# ── config.ps1 ─────────────────────────────────────────────────────
$ConfigPath = Join-Path $InstallDir "config.ps1"
@"
# agent-otel redaction proxy runtime config.
# Edit this file then restart the task to apply:
#   Stop-ScheduledTask -TaskName 'AgentOtel.RedactionProxy'
#   Start-ScheduledTask -TaskName 'AgentOtel.RedactionProxy'

`$RedactionProxyListen = '127.0.0.1:4319'
`$RedactionProxyUpstream = 'https://agent-otel-collector.kindbay-ee480b05.centralus.azurecontainerapps.io'
`$RedactionProxyPrivacyFilter = `$$($PII.ToString().ToLower())
`$RedactionProxyPfQuant = 'q4f16'
`$RedactionProxyPfCategories = 'secret,account_number,private_address,private_phone,private_email'
"@ | Set-Content -Path $ConfigPath

# ── render + register Scheduled Task ───────────────────────────────
$User = "$env:USERDOMAIN\$env:USERNAME"
$LauncherPath = Join-Path $InstallDir "launcher.ps1"

$xml = (Get-Content -Raw $TaskTpl).
    Replace("{{INSTALL_DIR}}", $InstallDir).
    Replace("{{LAUNCHER}}", $LauncherPath).
    Replace("{{USER}}", $User)

# Idempotent re-install: unregister first if present.
if (Get-ScheduledTask -TaskName "AgentOtel.RedactionProxy" -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName "AgentOtel.RedactionProxy" -Confirm:$false
}
Register-ScheduledTask -TaskName "AgentOtel.RedactionProxy" -Xml $xml | Out-Null
Start-ScheduledTask -TaskName "AgentOtel.RedactionProxy"
Info "registered+started Scheduled Task: AgentOtel.RedactionProxy"

# ── settings.json wiring ───────────────────────────────────────────
$Settings = Join-Path $env:USERPROFILE ".claude\settings.json"
if (Test-Path $Settings) {
    Copy-Item $Settings "$Settings.bak-$(Get-Date -UFormat %s)"
    $obj = Get-Content -Raw $Settings | ConvertFrom-Json
    if (-not $obj.env) { $obj | Add-Member -NotePropertyName env -NotePropertyValue (New-Object PSObject) }
    $obj.env.OTEL_EXPORTER_OTLP_ENDPOINT = "http://127.0.0.1:4319"
    $obj | ConvertTo-Json -Depth 100 | Set-Content -Path $Settings
    Info "patched $Settings to use proxy endpoint (backup taken)"
}

Write-Host ""
Info "Installed. Health check:"
Write-Host "  curl http://127.0.0.1:4319/healthz"
Write-Host ""
Info "Tail logs:"
Write-Host "  Get-Content -Wait $LogDir\redaction-proxy.out.log"
Write-Host ""
Info "Uninstall:  $PSScriptRoot\uninstall.ps1"
