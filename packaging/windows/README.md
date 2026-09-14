# Windows Distribution

Target user artifact:

- `VLUX_POS_Setup_<version>.exe`

Internal artifact:

- `VLUX_POS_<version>_x64.msi`

Architecture:

- WiX Toolset v4 Burn bootstrapper.
- MSI payload under `C:\Program Files\VLUX\POS`.
- Runtime data under `C:\ProgramData\VLUX\POS`.
- Data, filestore, logs and backups are separate from binaries.
- Uninstall preserves user data by default.

Windows service approach:

- Preferred production path is a small VLUX-owned service host that launches
  Odoo with a locked config and captures logs predictably.
- Third-party service wrappers are not approved until license, maintenance,
  uninstall behavior and log handling are reviewed.

Signing:

- `WINDOWS_SIGNING=PENDING`
- `GENERAL_AVAILABILITY_SIGNING_REQUIRED=YES`
- Do not use test certificates as production evidence.

Clean-machine validation is manual pending. The expected final UX remains one
file: run setup, open the final URL, operate POS.
