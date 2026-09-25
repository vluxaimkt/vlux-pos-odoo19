"""Benchmark the bulk catalog import with a store-sized spreadsheet.

Builds an XLSX with ``--rows`` rows: half update existing products (price
and stock), half create new ones (with category, taxes and stock). Measures
validation and import separately (wall time, SQL queries, peak Python
memory) and checks the result: every row applied exactly once, a second run
creates nothing.

    python tools/perf/bench_catalog_import.py -c odoo.conf -d vlux_perf_10k --rows 10000

The database is changed (that is the point): run it on a synthetic database.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import random
import sys
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _odoo_env import add_common_arguments, bootstrap, environment  # noqa: E402

NEW_PREFIX = "789"


def build_xlsx(existing, new_count, seed):
    import openpyxl

    rng = random.Random(seed)
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Productos"
    sheet.append(["codigo_barras", "nombre", "precio_venta", "costo", "referencia",
                  "categoria_pos", "categoria", "impuestos", "existencia", "inventariable", "disponible_pos"])
    for barcode in existing:
        sheet.append([barcode, "", round(rng.uniform(5, 500), 2), "", "", "", "", "", rng.randint(0, 200), "", ""])
    for index in range(new_count):
        sheet.append([f"{NEW_PREFIX}{seed % 1000:03d}{index:07d}", f"Producto importado {index}",
                      round(rng.uniform(5, 500), 2), round(rng.uniform(1, 300), 2), "",
                      f"Importados / Grupo {index % 25}", "", "", rng.randint(0, 200), "", ""])
    stream = io.BytesIO()
    book.save(stream)
    return stream.getvalue()


def measure(env, function):
    queries = env.cr.sql_log_count
    tracemalloc.start()
    started = time.perf_counter()
    function()
    elapsed = time.perf_counter() - started
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {"seconds": round(elapsed, 2), "queries": env.cr.sql_log_count - queries, "peak_mb": round(peak / 1e6, 1)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_arguments(parser)
    parser.add_argument("--rows", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=int(time.time()))
    parser.add_argument("--json", default="")
    parser.add_argument("--label", default="catalog_import")
    args = parser.parse_args()
    odoo = bootstrap(args)

    report = {"label": args.label, "rows": args.rows}
    with environment(odoo, args.db) as env:
        company = env.company
        updates = args.rows // 2
        existing = env["product.product"].search_read(
            [("barcode", "!=", False), ("company_id", "in", [False, company.id]),
             ("barcode", "not like", NEW_PREFIX + "%")], ["barcode"], limit=updates, order="id")
        existing = [row["barcode"] for row in existing]
        new_count = args.rows - len(existing)
        data = build_xlsx(existing, new_count, args.seed)
        report.update({"file_kb": round(len(data) / 1024), "updates": len(existing), "creates": new_count})
        warehouse = env["stock.warehouse"].search([("company_id", "=", company.id)], limit=1)
        Import = env["vlux.catalog.import"]

        def run(label):
            record = Import.create({"name": "bench.xlsx", "file": base64.b64encode(data),
                                    "location_id": warehouse.lot_stock_id.id})
            report[label + "_validate"] = measure(env, record.action_validate)
            record.action_import()
            report[label + "_import"] = measure(env, record._run_to_completion)
            report[label + "_result"] = {
                "state": record.state, "created": record.created_count, "updated": record.updated_count,
                "unchanged": record.unchanged_count, "stock": record.stock_count, "errors": record.error_count,
            }
            env.cr.commit()
            env.invalidate_all()

        run("first")
        run("second")
        report["rows_per_second_import"] = round(args.rows / report["first_import"]["seconds"], 1)
        report["exact"] = (
            report["first_result"]["created"] == new_count
            and report["first_result"]["updated"] + report["first_result"]["unchanged"] == len(existing)
            and report["first_result"]["errors"] == 0
            and report["second_result"]["created"] == 0
            and report["second_result"]["unchanged"] == args.rows
            and report["second_result"]["stock"] == 0
        )
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
