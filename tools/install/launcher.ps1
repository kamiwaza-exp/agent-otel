# Launcher for the agent-otel redaction proxy on Windows.
#
# Invoked by the Scheduled Task registered by install.ps1. Reads
# $InstallDir\config.ps1 for runtime settings, activates the bundled
# venv, and runs the proxy. Restart-on-failure is handled by Task
# Scheduler.
#
# Layout assumed:
#   $InstallDir\config.ps1           — port, categories, upstream
#   $InstallDir\venv\Scripts\python.exe
#   $InstallDir\redaction-proxy.py

$ErrorActionPreference = "Stop"

$InstallDir = $PSScriptRoot

# Defaults; overridden if config.ps1 exists.
$RedactionProxyListen = "127.0.0.1:4319"
$RedactionProxyUpstream = "https://agent-otel-collector.kindbay-ee480b05.centralus.azurecontainerapps.io"
$RedactionProxyPrivacyFilter = $false
$RedactionProxyPfQuant = "q4f16"
$RedactionProxyPfCategories = "secret,account_number,private_address,private_phone,private_email"

# Operator-edited config (port, categories, etc.) overrides defaults.
$ConfigPath = Join-Path $InstallDir "config.ps1"
if (Test-Path $ConfigPath) {
    . $ConfigPath
}

$PythonExe = Join-Path $InstallDir "venv\Scripts\python.exe"
$ProxyScript = Join-Path $InstallDir "redaction-proxy.py"

$Args = @(
    $ProxyScript,
    "--listen", $RedactionProxyListen,
    "--upstream", $RedactionProxyUpstream
)
if ($RedactionProxyPrivacyFilter) {
    $Args += @("--privacy-filter", "--quant", $RedactionProxyPfQuant, "--pf-categories", $RedactionProxyPfCategories)
}

# Replace this process so the Scheduled Task tracks the python process,
# not a transient powershell wrapper.
& $PythonExe @Args
exit $LASTEXITCODE
