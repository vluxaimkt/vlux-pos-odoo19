"""Break down the POS start-up payload (`pos.session.load_data`) by model and field.

Answers "where do the megabytes go": bytes, rows and share per model, the
heaviest fields of the biggest models, and which fields come from which addon
(`_load_pos_data_fields` owners are reported through `ir.model.fields.modules`).

    python tools/perf/pos_payload_breakdown.py -c odoo.conf -d vlux_perf --top-fields 12
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _odoo_env import add_common_arguments, bootstrap, environment  # noqa: E402


def _size(value) -> int:
    return len(json.dumps(value, default=str, separators=(",", ":")))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_arguments(parser)
    parser.add_argument("--config-name", default="")
    parser.add_argument("--top-models", type=int, default=12)
    parser.add_argument("--top-fields", type=int, default=10)
    parser.add_argument("--field-models", default="product.template,product.product",
                        help="Comma-separated models to break down per field")
    parser.add_argument("--json", default="")
    args = parser.parse_args()
    odoo = bootstrap(args)

    with environment(odoo, args.db, readonly=False) as env:
        domain = [("company_id", "=", env.company.id)]
        if args.config_name:
            domain.append(("name", "=", args.config_name))
        config = env["pos.config"].search(domain, limit=1)
        if not config:
            raise SystemExit("No pos.config found")
        session = env["pos.session"].search(
            [("config_id", "=", config.id), ("state", "in", ("opening_control", "opened"))], limit=1
        ) or env["pos.session"].create({"config_id": config.id, "user_id": env.uid})
        config_name = config.name
        data = session.load_data([])
        total = _size(data)

        models = []
        for model, records in data.items():
            rows = len(records) if isinstance(records, list) else None
            models.append({"model": model, "bytes": _size(records), "rows": rows})
        models.sort(key=lambda item: item["bytes"], reverse=True)
        for item in models:
            item["share_pct"] = round(item["bytes"] * 100.0 / total, 1)

        field_owner = {}
        for row in env["ir.model.fields"].sudo().search_read(
            [("model", "in", args.field_models.split(","))], ["model", "name", "modules"]
        ):
            field_owner[(row["model"], row["name"])] = row["modules"]

        fields_report = {}
        for model in args.field_models.split(","):
            records = data.get(model)
            if not isinstance(records, list) or not records:
                continue
            per_field: dict[str, int] = {}
            for record in records:
                for name, value in record.items():
                    per_field[name] = per_field.get(name, 0) + _size(value) + len(name) + 4
            model_bytes = _size(records)
            ranked = sorted(per_field.items(), key=lambda item: item[1], reverse=True)
            fields_report[model] = {
                "fields": len(per_field),
                "bytes_per_row": round(model_bytes / len(records)),
                "top": [
                    {
                        "field": name,
                        "bytes": size,
                        "share_pct": round(size * 100.0 / model_bytes, 1),
                        "modules": field_owner.get((model, name), ""),
                    }
                    for name, size in ranked[: args.top_fields]
                ],
                "vlux_fields": sorted(
                    name for name in per_field if "vlux" in (field_owner.get((model, name)) or "")
                ),
            }
        env.cr.rollback()

    report = {
        "db": args.db,
        "pos_config": config_name,
        "payload_bytes": total,
        "models": models[: args.top_models],
        "model_count": len(models),
        "fields": fields_report,
    }
    print(json.dumps(report, indent=2))
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
