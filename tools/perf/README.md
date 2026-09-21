# VLUX POS performance tooling

Reproducible, synthetic-data benchmarks. Nothing here touches real client
data: every generator refuses database names containing `prod` or `real`, and
all catalog/sales content is generated.

All tools accept the same connection arguments:

| Flag | Meaning |
| --- | --- |
| `--odoo-home` | Odoo checkout (or set `ODOO_HOME`) |
| `-c/--config` | `odoo.conf` with database credentials (never versioned) |
| `-d/--db` | Target database |
| `--json FILE` | Write the report as JSON for `compare.py` |

## Datasets

```bash
python tools/perf/seed_synthetic.py -c odoo.conf -d vlux_perf --products 1000  --orders 1000
python tools/perf/seed_synthetic.py -c odoo.conf -d vlux_perf --products 10000 --orders 10000
python tools/perf/seed_synthetic.py -c odoo.conf -d vlux_perf --products 50000 --orders 100000
```

The generator is idempotent per size (it tops up to the requested count).
Orders are spread over `--days` with ~15 % on the current day so the Owner
dashboard has "today" data. Approximate cost on a 2 vCPU host: ~55 products/s,
~65 orders/s, so the 50k/100k profile is a nightly/manual job, not a CI step.

## Benchmarks

| Tool | Measures | Needs HTTP server |
| --- | --- | --- |
| `bench_owner.py` | Owner dashboard latency p50/p95, SQL queries per call, ORM records materialised | no (`--cache cold` default clears the dashboard cache per call; `warm` measures hits) |
| `bench_pos_load.py` | `pos.session.load_data` latency, query count, payload bytes, product rows (`--incremental`: warm re-open with the browser's IndexedDB cache, i.e. only records changed since the last sync) | no |
| `pos_payload_breakdown.py` | Where the POS start-up payload goes: bytes/rows per model, heaviest fields and the addon that owns each | no |
| `bench_scanner.py` | HTTP requests per scan, scan→result latency, lost/duplicated scans, throughput | yes |
| `bench_startup.py` | Time to `/vlux/health`, idle RSS, idle PostgreSQL connections | spawns one |
| `bench_api_burst.py` | API v1 under a concurrent burst: status histogram, latency, whether the 600/min budget held with zero 500s | yes |
| `bench_catalog_sync.py` | API v1 catalog feed: pages, latency and SQL queries per page for a full sync, exactness of an incremental sync (changes + deletions); `--base-url` adds end-to-end HTTP latency | no (`--base-url` needs one) |

`bench_scanner.py` plays both sides of the protocol: the phone (strategies
`v1`, `batch`, `push`) and the POS (ORM acknowledgements after
`--ack-delay-ms`). Use `--distinct-barcodes 1` to measure intentional repeats.

## Comparing

```bash
python tools/perf/compare.py before/owner.json after/owner.json \
    --budget latency_ms.p95:<=:0.7 \
    --budget sql_queries_per_call.max:<=:abs:40
```

Budgets are either relative (`AFTER / BEFORE <= 0.7`) or absolute
(`abs:` prefix). CI runs the small profile with generous absolute limits; the
relative comparison is what matters when validating a change.
