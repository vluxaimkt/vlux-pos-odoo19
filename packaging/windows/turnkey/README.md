# VLUX POS Windows Turnkey Lab

This payload is the first Windows turnkey installer workstream. It embeds the
VLUX-owned service host and preserves the existing VLUX MSI/Burn build factory.

Current status:

- Service host: embedded and installed as `VLUXPOS`.
- Product payload: sanitized client payload under `C:\Program Files\VLUX\POS`.
- Data roots: `C:\ProgramData\VLUX\POS`.
- Runtime chain: pending. Python 3.12.10, PostgreSQL 16.14, Odoo pinned runtime, Python wheels and HTTPS proxy are not yet embedded as a complete offline chain.

Do not mark production or clean-machine PASS until the runtime chain is embedded
or downloaded with pinned checksums and the Windows VM install test passes.
