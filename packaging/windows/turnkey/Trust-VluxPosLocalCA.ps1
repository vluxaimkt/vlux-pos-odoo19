Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProgramDataRoot = "C:\ProgramData\VLUX\POS"
$CaddyRootCa = Join-Path $ProgramDataRoot "caddy\data\caddy\pki\authorities\local\root.crt"
$PublicCertDir = Join-Path $ProgramDataRoot "certificates"
$PublicCert = Join-Path $PublicCertDir "VLUX_POS_Local_CA.crt"
$FirewallRuleName = "VLUX POS HTTPS 8443"

New-Item -ItemType Directory -Force -Path $PublicCertDir | Out-Null

$deadline = (Get-Date).AddSeconds(180)
while (!(Test-Path -LiteralPath $CaddyRootCa) -and (Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 1
}

if (!(Test-Path -LiteralPath $CaddyRootCa)) {
    throw "VLUX POS Caddy root CA was not generated at $CaddyRootCa"
}

Copy-Item -LiteralPath $CaddyRootCa -Destination $PublicCert -Force

$cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($PublicCert)
if (!$cert.HasPrivateKey -and $cert.Subject -match "Caddy Local Authority") {
    $existing = Get-ChildItem Cert:\LocalMachine\Root | Where-Object { $_.Thumbprint -eq $cert.Thumbprint } | Select-Object -First 1
    if ($null -eq $existing) {
        $store = New-Object System.Security.Cryptography.X509Certificates.X509Store("Root", "LocalMachine")
        $store.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
        try {
            $store.Add($cert)
        } finally {
            $store.Close()
        }
    }
} else {
    throw "Refusing to trust unexpected VLUX POS CA certificate"
}

$rule = Get-NetFirewallRule -DisplayName $FirewallRuleName -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -eq $rule) {
    New-NetFirewallRule `
        -DisplayName $FirewallRuleName `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort 8443 `
        -Program "C:\Program Files\VLUX\POS\runtime\caddy\caddy.exe" `
        -Profile Domain,Private | Out-Null
}
