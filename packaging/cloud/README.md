# Cloud Managed Distribution

Cloud is provisioned by VLUX, not by the customer.

The app image contains:

- Odoo 19 pinned to `a2d73c5900d8886d115afe1ccb7f5c97c7e71a97`.
- Python 3.12.10 base runtime.
- Productive VLUX addons: `vlux_core`, `vlux_mobile_scanner`, `vlux_owner`.

PostgreSQL is not embedded in the app container. The compose reference pins
PostgreSQL 16 (`postgres:16.14`), keeps PostgreSQL private on the internal
network, and exposes only Caddy on 80/443.

Cloud database policy:

- `CLOUD_DB_ENGINE=PostgreSQL`
- `CLOUD_DB_MAJOR=16`
- `CLOUD_DB_PRIMARY=STANDARD_POSTGRESQL`
- `SUPABASE_PRIMARY_DB=NO`

Tenant isolation is directory and database based in this reference bundle:

- one tenant maps to one Odoo database
- one tenant maps to one database role and secret set
- independent PostgreSQL data namespace
- independent filestore
- independent backup namespace
- independent domain/Caddy config

Production orchestration may replace compose with managed infrastructure, but
must preserve one database, one DB role/secrets set and one filestore per
client. PostgreSQL must never be exposed publicly.
