# VLUX POS Windows Runtime License Audit

This document records the technical license inventory for the Windows turnkey
installer workstream. It is not a commercial release notice.

- Odoo Community: LGPL-3.0, pinned to commit `a2d73c5900d8886d115afe1ccb7f5c97c7e71a97`.
- Python: Python Software Foundation License, target version 3.12.10.
- PostgreSQL: PostgreSQL License, target compatible baseline 16.14.
- Caddy: Apache-2.0, candidate HTTPS reverse proxy for local Windows deployments.
- .NET service host: VLUX-owned source in this repository; .NET runtime redistribution must follow Microsoft terms when bundled self-contained.
- Python dependencies: inherited from pinned Odoo requirements plus `requirements-extra.txt`; wheel redistribution review remains required before declaring offline installer status.

`THIRD_PARTY_LICENSE_AUDIT=PARTIAL_PASS_RUNTIME_REVIEW_REQUIRED`
