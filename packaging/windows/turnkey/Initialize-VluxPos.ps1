param(
    [string]$BusinessName = "VLUX POS",
    [string]$Edition = "local_complete"
)

$ErrorActionPreference = "Stop"
$programData = Join-Path $env:ProgramData "VLUX\POS"
$paths = @(
    (Join-Path $programData "config"),
    (Join-Path $programData "data"),
    (Join-Path $programData "filestore"),
    (Join-Path $programData "backups"),
    (Join-Path $programData "logs")
)

foreach ($path in $paths) {
    New-Item -ItemType Directory -Force -Path $path | Out-Null
}

$metadata = @{
    business_name = $BusinessName
    edition = $Edition
    runtime_chain_status = "PENDING_RUNTIME_CHAIN"
    generated_at = (Get-Date).ToUniversalTime().ToString("o")
}

$metadata | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 (Join-Path $programData "config\vlux-setup.json")
Write-Host "VLUX POS initial setup metadata written. Runtime chain is still pending."
