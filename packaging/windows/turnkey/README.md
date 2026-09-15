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
