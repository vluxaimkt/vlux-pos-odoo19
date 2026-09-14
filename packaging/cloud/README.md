# Cloud Managed Distribution

Cloud is provisioned by VLUX, not by the customer.

The app image contains:

- Odoo 19 pinned to `a2d73c5900d8886d115afe1ccb7f5c97c7e71a97`.
- Python 3.12.10 base runtime.
- Productive VLUX addons: `vlux_core`, `vlux_mobile_scanner`, `vlux_owner`.

PostgreSQL is not embedded in the app container. The compose reference keeps
PostgreSQL private on the internal network and exposes only Caddy on 80/443.

Tenant isolation is directory and database based in this reference bundle:

- independent PostgreSQL data
- independent database name
- independent filestore
- independent secrets
- independent backups
- independent domain/Caddy config

Production orchestration may replace compose with managed infrastructure, but
must preserve one database and one filestore per client.
