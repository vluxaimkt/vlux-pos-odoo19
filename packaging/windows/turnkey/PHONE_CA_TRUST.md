# VLUX POS Local CA Trust

VLUX POS Local Complete exports the public local CA certificate to:

`C:\ProgramData\VLUX\POS\certificates\VLUX_POS_Local_CA.crt`

This file is public certificate material. Do not copy or share files ending in `.key`.

## Android

1. Copy `VLUX_POS_Local_CA.crt` to the phone using a trusted local method.
2. Open Android settings and install it as a CA certificate.
3. Android may show a network monitoring warning for user-installed CAs.
4. Open `https://<VLUX LAN name or IP>:8443/` in the browser and confirm it is trusted.

Exact menu names vary by Android vendor and version.

## iPhone / iPad

1. Copy `VLUX_POS_Local_CA.crt` to the device using a trusted local method.
2. Install the certificate/profile from iOS settings.
3. Go to certificate trust settings and enable full trust for the VLUX POS local root certificate.
4. Open `https://<VLUX LAN name or IP>:8443/` in Safari and confirm it is trusted.

Exact menu names vary by iOS version.

## Notes

- Phone camera scanner validation still requires a physical device test.
- HTTP port `8080` is for local/lab diagnostics.
- HTTPS port `8443` is required for browser camera/mobile scanner flows.
