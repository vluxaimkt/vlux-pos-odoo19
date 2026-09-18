# VLUX POS Windows Turnkey Lab

This payload is the Windows turnkey installer workstream. It embeds the
VLUX-owned service host and preserves the existing VLUX MSI/Burn build factory.

Current status:

- Service host: embedded and installed as `VLUXPOS`.
- Product payload: sanitized client payload under `C:\Program Files\VLUX\POS`.
- Private runtime payload: Python 3.12.10, pinned Odoo source, PostgreSQL 16.14 binaries, offline wheelhouse and Caddy.
- Data roots: `C:\ProgramData\VLUX\POS`.
- Runtime chain: complete for lab validation. Client install must not download Python, PostgreSQL, Odoo, wheels, Caddy or Git content.

Do not mark production or clean-machine PASS until the pristine Windows VM test,
real phone scanner test and production signing gate pass.

## Security on first start

The `VLUXPOS` service (runs as `NT AUTHORITY\LocalService`) enforces two rules
on every start:

- **Data tree ACL.** `C:\ProgramData\VLUX\POS` gets a protected DACL that only
  grants SYSTEM, Administrators and LocalService; existing children are reset
  once to inherit it (`config\.acl-hardened-v1` marks that). The default
  ProgramData ACL would otherwise let every local account (e.g. a cashier on a
  shared POS PC) read `config\secrets.json` and `config\odoo.conf`, which hold
  the PostgreSQL superuser, database and Odoo master passwords. The service
  refuses to start if either file is still readable by Users, Everyone,
  Authenticated Users or Interactive.
- **No default `admin/admin`.** Odoo seeds `admin/admin` and Caddy publishes
  Odoo on the LAN (`:8443`). While the admin password is still `admin`, the
  service replaces it with the generated `InitialOwnerPassword`; a password the
  owner already changed is never overwritten.

The owner's first login is `admin` with the initial password, which a Windows
administrator reads with:

```powershell
(Get-Content C:\ProgramData\VLUX\POS\config\secrets.json -Raw | ConvertFrom-Json).InitialOwnerPassword
```

Change it right after the first login (avatar -> Preferences -> Account
Security). The Windows turnkey CI asserts that `admin/admin` is rejected, that
the initial password works and that both secret files are not readable by
ordinary accounts.
