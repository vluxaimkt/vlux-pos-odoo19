"""Generate SYNTHETIC catalog and sales datasets for VLUX performance work.

Everything produced here is fictional: EAN-13 codes in the 200-299 internal
range, generated names and random prices. Never point this tool at a database
holding real client data.

    python tools/perf/seed_synthetic.py -c odoo.conf -d vlux_perf \\
        --products 10000 --orders 10000 --days 30

The tool is idempotent per profile: products are keyed by default_code and
orders by a synthetic pos_reference prefix, so re-running tops up instead of
duplicating.
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _odoo_env import add_common_arguments, bootstrap, environment  # noqa: E402

PRODUCT_BATCH = 500
ORDER_BATCH = 200
CATEGORIES = ("Abarrotes", "Bebidas", "Botanas", "Lacteos", "Limpieza", "Panaderia", "Farmacia", "Papeleria")
ADJECTIVES = ("Clasico", "Familiar", "Ligero", "Premium", "Natural", "Mini", "Extra", "Original")
NOUNS = ("Refresco", "Galleta", "Jabon", "Leche", "Pan", "Jugo", "Yogurt", "Cereal", "Sopa", "Cafe", "Arroz", "Frijol")
SEED_PREFIX = "VLUXPERF"


def ean13(seed: int) -> str:
    body = f"2{seed:011d}"[:12]
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(body))
    return body + str((10 - total % 10) % 10)


def ensure_categories(env):
    Category = env["pos.category"]
    result = []
    for name in CATEGORIES:
        categ = Category.search([("name", "=", f"{SEED_PREFIX} {name}")], limit=1)
        if not categ:
            categ = Category.create({"name": f"{SEED_PREFIX} {name}"})
        result.append(categ)
    return result


def seed_products(env, count: int, rng: random.Random, log) -> None:
    Template = env["product.template"].with_context(tracking_disable=True, mail_create_nolog=True)
    existing = env["product.product"].with_context(active_test=False).search_count(
        [("default_code", "=like", f"{SEED_PREFIX}-%")]
    )
    if existing >= count:
        log(f"products: {existing} synthetic products already present (target {count})")
        return
    categories = ensure_categories(env)
    taxes = env["account.tax"].search(
        [("type_tax_use", "=", "sale"), ("company_id", "=", env.company.id)], limit=1
    )
    started = time.perf_counter()
    created = 0
    index = existing
    while index < count:
        batch = []
        for _ in range(min(PRODUCT_BATCH, count - index)):
            index += 1
            name = f"{rng.choice(NOUNS)} {rng.choice(ADJECTIVES)} {index}"
            batch.append({
                "name": name,
                "default_code": f"{SEED_PREFIX}-{index:06d}",
                "barcode": ean13(index),
                "list_price": round(rng.uniform(5, 450), 2),
                "standard_price": round(rng.uniform(2, 300), 2),
                "type": "consu",
                "is_storable": True,
                "available_in_pos": True,
                "sale_ok": True,
                "pos_categ_ids": [(6, 0, [rng.choice(categories).id])],
                "taxes_id": [(6, 0, taxes.ids)],
            })
        Template.create(batch)
        created += len(batch)
        env.cr.commit()
        env.invalidate_all()
        log(f"products: {index}/{count} ({time.perf_counter() - started:.1f}s)")
    log(f"products: created {created} in {time.perf_counter() - started:.1f}s")


def seed_stock(env, rng: random.Random, log) -> None:
    """Synthetic on-hand quantities so low-stock and inventory paths have data."""
    warehouse = env["stock.warehouse"].search([("company_id", "=", env.company.id)], limit=1)
    if not warehouse:
        log("stock: no warehouse, skipping")
        return
    location = warehouse.lot_stock_id
    Quant = env["stock.quant"].sudo()
    products = env["product.product"].search([("default_code", "=like", f"{SEED_PREFIX}-%")])
    quanted = {
        row["product_id"][0]
        for row in Quant.search_read(
            [("product_id", "in", products.ids), ("location_id", "=", location.id)], ["product_id"]
        )
    }
    missing = [p for p in products if p.id not in quanted]
    if not missing:
        log("stock: quants already present")
        return
    started = time.perf_counter()
    for start in range(0, len(missing), PRODUCT_BATCH):
        chunk = missing[start:start + PRODUCT_BATCH]
        Quant.create([
            {
                "product_id": product.id,
                "location_id": location.id,
                "quantity": rng.choice([0, 1, 2, 3, 5, 8, 12, 25, 40, 75, 120]),
            }
            for product in chunk
        ])
        env.cr.commit()
        env.invalidate_all()
    log(f"stock: {len(missing)} quants in {time.perf_counter() - started:.1f}s")


def ensure_configs(env, count: int):
    configs = env["pos.config"].search(
        [("name", "=like", f"{SEED_PREFIX} Caja%"), ("company_id", "=", env.company.id)]
    )
    for index in range(len(configs), count):
        configs |= env["pos.config"].create(
            {"name": f"{SEED_PREFIX} Caja {index + 1}", "company_id": env.company.id}
        )
    return configs


def ensure_sessions(env, configs):
    sessions = env["pos.session"]
    for config in configs:
        session = env["pos.session"].search(
            [("config_id", "=", config.id), ("name", "=like", f"{SEED_PREFIX}%")], limit=1
        )
        if not session:
            session = env["pos.session"].create(
                {"config_id": config.id, "user_id": env.uid, "name": f"{SEED_PREFIX}/{config.id}"}
            )
            session.write({"state": "closed", "stop_at": datetime.now()})
        sessions |= session
    return sessions


def seed_orders(env, count: int, days: int, configs: int, rng: random.Random, log) -> None:
    Order = env["pos.order"].with_context(tracking_disable=True)
    existing = Order.search_count([("pos_reference", "=like", f"{SEED_PREFIX}/%")])
    if existing >= count:
        log(f"orders: {existing} synthetic orders already present (target {count})")
        return
    products = env["product.product"].search_read(
        [("default_code", "=like", f"{SEED_PREFIX}-%")], ["id", "list_price", "taxes_id"], limit=20000
    )
    if not products:
        raise SystemExit("orders: seed products first (--products N)")
    sessions = ensure_sessions(env, ensure_configs(env, configs))
    taxes = env["account.tax"].browse({tax for p in products for tax in p["taxes_id"]})
    tax_rate = sum(taxes.mapped("amount")) / 100.0 if taxes else 0.0
    price_includes = bool(taxes and all(taxes.mapped("price_include")))
    now = datetime.utcnow()
    started = time.perf_counter()
    index = existing
    while index < count:
        batch = []
        for _ in range(min(ORDER_BATCH, count - index)):
            index += 1
            session = rng.choice(sessions)
            # Bias order times towards the current day so the dashboard has "today" data.
            day_offset = 0 if rng.random() < 0.15 else rng.randint(0, max(days - 1, 0))
            date_order = now - timedelta(days=day_offset, hours=rng.uniform(0, 14), minutes=rng.uniform(0, 59))
            lines = []
            total_excl = total_incl = 0.0
            for _ in range(rng.randint(1, 6)):
                product = rng.choice(products)
                qty = rng.choice([1, 1, 1, 2, 3])
                unit = product["list_price"]
                if price_includes:
                    subtotal_incl = round(unit * qty, 2)
                else:
                    subtotal_incl = round(unit * qty * (1 + tax_rate), 2)
                subtotal = round(subtotal_incl / (1 + tax_rate), 2) if tax_rate else subtotal_incl
                total_excl += subtotal
                total_incl += subtotal_incl
                lines.append((0, 0, {
                    "name": f"{SEED_PREFIX} line",
                    "product_id": product["id"],
                    "qty": qty,
                    "price_unit": unit,
                    "price_subtotal": subtotal,
                    "price_subtotal_incl": subtotal_incl,
                    "tax_ids": [(6, 0, product["taxes_id"])],
                }))
            batch.append({
                "session_id": session.id,
                "company_id": env.company.id,
                "pos_reference": f"{SEED_PREFIX}/{index:07d}",
                "date_order": date_order,
                "state": "paid",
                "amount_tax": round(total_incl - total_excl, 2),
                "amount_total": round(total_incl, 2),
                "amount_paid": round(total_incl, 2),
                "amount_return": 0.0,
                "lines": lines,
            })
        Order.create(batch)
        env.cr.commit()
        env.invalidate_all()
        log(f"orders: {index}/{count} ({time.perf_counter() - started:.1f}s)")
    log(f"orders: done in {time.perf_counter() - started:.1f}s")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    add_common_arguments(parser)
    parser.add_argument("--products", type=int, default=0)
    parser.add_argument("--orders", type=int, default=0)
    parser.add_argument("--days", type=int, default=30, help="Spread orders over the last N days")
    parser.add_argument("--configs", type=int, default=3, help="Synthetic POS registers")
    parser.add_argument("--seed", type=int, default=20260917)
    parser.add_argument("--no-stock", action="store_true")
    args = parser.parse_args()
    if "prod" in args.db.lower() or "real" in args.db.lower():
        raise SystemExit("Refusing to seed a database whose name suggests real data.")

    rng = random.Random(args.seed)
    odoo = bootstrap(args)

    def log(message: str) -> None:
        print(f"[seed] {message}", flush=True)

    with environment(odoo, args.db) as env:
        if args.products:
            seed_products(env, args.products, rng, log)
            if not args.no_stock:
                seed_stock(env, rng, log)
        if args.orders:
            seed_orders(env, args.orders, args.days, args.configs, rng, log)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
