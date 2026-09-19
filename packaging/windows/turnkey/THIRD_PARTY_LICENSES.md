# VLUX POS Windows Runtime License Audit

This document records the technical license inventory for the Windows turnkey
installer workstream. It is not a commercial release notice.

- Odoo Community: LGPL-3.0, pinned to commit `a2d73c5900d8886d115afe1ccb7f5c97c7e71a97`.
- Python: Python Software Foundation License, version 3.12.10, distributed from the official python.org Windows embeddable package.
- PostgreSQL: PostgreSQL License, version 16.14 Windows x64 binaries from EnterpriseDB/PostgreSQL distribution infrastructure.
- Caddy: Apache-2.0, version 2.10.2 Windows x64 binary from the official Caddy GitHub release.
- .NET service host: VLUX-owned source in this repository; published self-contained for Windows x64.
- Python dependencies: resolved at build time from pinned Odoo requirements plus `requirements-extra.txt`, stored in an offline wheelhouse, and installed into the private Python runtime with `--no-index`.

`THIRD_PARTY_LICENSE_AUDIT=PASS_FOR_LAB_RUNTIME`
