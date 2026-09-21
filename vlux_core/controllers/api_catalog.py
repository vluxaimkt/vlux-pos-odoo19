"""VLUX API v1, phase B: the store's data for a register that keeps a local copy.

Two kinds of endpoint:

* **Change feeds** (``/catalog/products``, ``/catalog/customers``): paged by
  an opaque cursor, ordered by ``(sync stamp, id)``. Each page carries the
  records that changed after the cursor plus the ids deleted in the same
  span, so a client applies the page, then the deletions, then stores
  ``next_cursor``. Retrying a page is harmless. Archived or de-listed records
  are still delivered with their flags; only real deletions come as ids.
* **Snapshots** (categories, taxes, pricelists, store config): small tables
  returned whole on every call.

Records newer than ``SETTLE_SECONDS`` are held back until the next call, so a
write that is still being committed by another worker cannot slip behind a
cursor that already passed its stamp.
"""
from datetime import datetime, timedelta

from odoo import fields, http
from odoo.http import request

from odoo.addons.vlux_core.controllers.api import (
    VluxApiError, api_route, decode_cursor, encode_cursor, int_param,
)
from odoo.addons.vlux_core.models.catalog_sync import TOMBSTONE_RETENTION_DAYS

PAGE_DEFAULT = 500
PAGE_MAX = 1000
# Longest transaction we assume can be writing catalog rows concurrently.
SETTLE_SECONDS = 30

PAGE_PARAMS = [
    {"name": "cursor", "in": "query", "schema": {"type": "string"},
     "description": "Cursor opaco devuelto en next_cursor; vacío para empezar desde cero."},
    {"name": "limit", "in": "query", "schema": {"type": "integer", "default": PAGE_DEFAULT, "maximum": PAGE_MAX}},
]


def _iso(value):
    """UTC timestamp with microseconds: what the cursor orders by."""
    if not value:
        return None
    if not hasattr(value, "microsecond"):  # a date
        return value.isoformat()
    return value.isoformat(timespec="microseconds") + "Z"


def _feed(env, model, stamp_field, cursor, limit, serialize):
    """One page of ``model`` after ``cursor``, with the deletions of its span.

    Stamps have one-second precision (``fields.Datetime.now``), so the page
    only covers stamps strictly before the horizon second: a record written
    later in the same second cannot fall behind a cursor already placed there.
    """
    since_stamp, since_id = decode_cursor(cursor)
    now = fields.Datetime.now()
    horizon = now - timedelta(seconds=SETTLE_SECONDS)
    fresh_start = since_stamp == datetime.min
    if not fresh_start and since_stamp < now - timedelta(days=TOMBSTONE_RETENTION_DAYS):
        raise VluxApiError(
            "RESYNC_REQUIRED",
            "El cursor es más antiguo que el historial de bajas (%d días); sincroniza desde cero."
            % TOMBSTONE_RETENTION_DAYS,
        )
    domain = [
        (stamp_field, "<", horizon),
        "|", (stamp_field, ">", since_stamp),
        "&", (stamp_field, "=", since_stamp), ("id", ">", since_id),
    ]
    records = env[model].with_context(active_test=False).search(
        domain, order=f"{stamp_field} asc, id asc", limit=limit + 1,
    )
    has_more = len(records) > limit
    records = records[:limit]
    if has_more:
        # Stop exactly at the last delivered record; the next page continues there.
        last = records[-1]
        next_stamp, next_id = last[stamp_field], last.id
        deleted_until = (next_stamp, next_id)
    elif since_stamp >= horizon:
        # Nothing can be behind the horizon yet: stay put.
        next_stamp, next_id = since_stamp, since_id
        deleted_until = None
    else:
        # Everything before the horizon was delivered: the cursor jumps there
        # so the deletions of this span are reported once, not forever.
        next_stamp, next_id = horizon, 0
        deleted_until = (horizon, 0)
    deleted = []
    if not fresh_start and deleted_until:
        deleted = env["vlux.catalog.tombstone"]._between(
            model, env.company, (since_stamp, since_id), deleted_until,
        )
    return {
        "items": [serialize(record) for record in records],
        "deleted": deleted,
        "next_cursor": encode_cursor(next_stamp, next_id),
        "has_more": has_more,
        "server_time": _iso(now),
    }


def _product_payload(product):
    # Template fields are read on the template on purpose: the variant's
    # related fields recompute through cache writes with access checks and
    # domain filtering, which is what made a 500-row page cost 380 ms.
    template = product.product_tmpl_id
    values = product.product_template_attribute_value_ids
    name = template.name
    if values:
        name = "%s (%s)" % (name, ", ".join(values.mapped("name")))
    return {
        "id": product.id,
        "template_id": template.id,
        "name": name,
        "barcode": product.barcode or None,
        "default_code": product.default_code or None,
        "list_price": template.list_price + sum(values.mapped("price_extra")),
        "currency_id": template.currency_id.id,
        "tax_ids": template.taxes_id.ids,
        "pos_category_ids": template.pos_categ_ids.ids,
        "category_id": template.categ_id.id,
        "uom": {"id": template.uom_id.id, "name": template.uom_id.name},
        "type": template.type,
        "is_storable": template.is_storable,
        "attributes": [
            {"attribute": value.attribute_id.name, "value": value.name} for value in values
        ],
        "active": product.active and template.active,
        "available_in_pos": template.available_in_pos,
        "sale_ok": template.sale_ok,
        "company_id": template.company_id.id or None,
        "sync_date": _iso(product.vlux_sync_date),
    }


def _customer_payload(partner):
    return {
        "id": partner.id,
        "name": partner.name,
        "is_company": partner.is_company,
        "parent_id": partner.parent_id.id or None,
        "email": partner.email or None,
        "phone": partner.phone or None,
        "vat": partner.vat or None,
        "street": partner.street or None,
        "street2": partner.street2 or None,
        "city": partner.city or None,
        "zip": partner.zip or None,
        "state": partner.state_id.code or None,
        "country": partner.country_id.code or None,
        "barcode": partner.barcode or None,
        "pricelist_id": partner.property_product_pricelist.id or None,
        "active": partner.active,
        "company_id": partner.company_id.id or None,
        "sync_date": _iso(partner.write_date),
    }


def _tax_payload(tax):
    return {
        "id": tax.id,
        "name": tax.name,
        "amount": tax.amount,
        "amount_type": tax.amount_type,
        "price_include": tax.price_include,
        "include_base_amount": tax.include_base_amount,
        "is_base_affected": tax.is_base_affected,
        "sequence": tax.sequence,
        "children_tax_ids": tax.children_tax_ids.ids,
        "active": tax.active,
    }


def _pricelist_payload(pricelist):
    return {
        "id": pricelist.id,
        "name": pricelist.name,
        "currency_id": pricelist.currency_id.id,
        "active": pricelist.active,
        "items": [
            {
                "id": item.id,
                "applied_on": item.applied_on,
                "product_template_id": item.product_tmpl_id.id or None,
                "product_id": item.product_id.id or None,
                "category_id": item.categ_id.id or None,
                "min_quantity": item.min_quantity,
                "date_start": _iso(item.date_start),
                "date_end": _iso(item.date_end),
                "compute_price": item.compute_price,
                "fixed_price": item.fixed_price,
                "percent_price": item.percent_price,
                "base": item.base,
                "base_pricelist_id": item.base_pricelist_id.id or None,
                "price_discount": item.price_discount,
                "price_surcharge": item.price_surcharge,
                "price_round": item.price_round,
                "price_min_margin": item.price_min_margin,
                "price_max_margin": item.price_max_margin,
            }
            for item in pricelist.item_ids
        ],
    }


def _register_payload(config):
    return {
        "id": config.id,
        "name": config.name,
        "currency_id": config.currency_id.id,
        "pricelist_id": config.pricelist_id.id or None,
        "available_pricelist_ids": config.available_pricelist_ids.ids,
        "use_pricelist": config.use_pricelist,
        "tax_display": config.iface_tax_included,
        "limit_categories": config.limit_categories,
        "available_pos_category_ids": config.iface_available_categ_ids.ids,
        "payment_methods": [
            {"id": method.id, "name": method.name, "type": method.type, "is_cash": method.is_cash_count}
            for method in config.payment_method_ids
        ],
        "receipt_header": config.receipt_header or None,
        "receipt_footer": config.receipt_footer or None,
    }


class VluxApiCatalog(http.Controller):

    @api_route("/catalog/products", scope="catalog:read", params=PAGE_PARAMS,
               summary="Productos (variantes) cambiados después del cursor, con bajas")
    def products(self, token, cursor=None, limit=None, **kwargs):
        """Change feed of sellable variants.

        Ordered by ``(vlux_sync_date, id)``. Archived products and products
        removed from the POS are delivered with ``active``/``available_in_pos``
        false; ``deleted`` lists the ids that no longer exist. Prices are the
        variant's list price in the company currency; pricelists apply on top.
        """
        limit = int_param(limit, "limit", PAGE_DEFAULT, 1, PAGE_MAX)
        return _feed(request.env, "product.product", "vlux_sync_date", cursor, limit, _product_payload)

    @api_route("/catalog/customers", scope="catalog:read", params=PAGE_PARAMS,
               summary="Clientes cambiados después del cursor, con bajas")
    def customers(self, token, cursor=None, limit=None, **kwargs):
        """Change feed of partners, ordered by ``(write_date, id)``."""
        limit = int_param(limit, "limit", PAGE_DEFAULT, 1, PAGE_MAX)
        return _feed(request.env, "res.partner", "write_date", cursor, limit, _customer_payload)

    @api_route("/catalog/pos-categories", scope="catalog:read", summary="Categorías del punto de venta")
    def pos_categories(self, token, **kwargs):
        """Snapshot of ``pos.category`` (the tiles a register shows)."""
        categories = request.env["pos.category"].search([], order="sequence, id")
        return {
            "items": [
                {
                    "id": category.id,
                    "name": category.name,
                    "parent_id": category.parent_id.id or None,
                    "sequence": category.sequence,
                    "has_image": bool(category.image_128),
                }
                for category in categories
            ],
        }

    @api_route("/catalog/categories", scope="catalog:read", summary="Categorías internas de producto")
    def categories(self, token, **kwargs):
        """Snapshot of ``product.category`` (accounting/reporting hierarchy)."""
        categories = request.env["product.category"].search([], order="complete_name")
        return {
            "items": [
                {"id": c.id, "name": c.name, "complete_name": c.complete_name, "parent_id": c.parent_id.id or None}
                for c in categories
            ],
        }

    @api_route("/catalog/taxes", scope="catalog:read", summary="Impuestos de venta de la compañía")
    def taxes(self, token, **kwargs):
        """Snapshot of the company's sale taxes, archived ones included."""
        taxes = request.env["account.tax"].with_context(active_test=False).search(
            [("type_tax_use", "=", "sale"), ("company_id", "=", request.env.company.id)],
            order="sequence, id",
        )
        return {"items": [_tax_payload(tax) for tax in taxes]}

    @api_route("/catalog/pricelists", scope="catalog:read", summary="Listas de precios con sus reglas")
    def pricelists(self, token, **kwargs):
        """Snapshot of the pricelists a register may use, rules included."""
        pricelists = request.env["product.pricelist"].with_context(active_test=False).search(
            ["|", ("company_id", "=", False), ("company_id", "=", request.env.company.id)],
            order="sequence, id",
        )
        return {"items": [_pricelist_payload(pricelist) for pricelist in pricelists]}

    @api_route("/store/config", scope="system:read", summary="Compañía, moneda, datos fiscales y cajas")
    def store_config(self, token, **kwargs):
        """What a register needs before it sells: company, currency, taxes by default and registers."""
        company = request.env.company
        currency = company.currency_id
        registers = request.env["pos.config"].search([("company_id", "=", company.id)], order="name")
        return {
            "company": {
                "id": company.id,
                "name": company.name,
                "vat": company.vat or None,
                "fiscal_regime": company.vlux_fiscal_regime or None,
                "receipt_legend": company.vlux_receipt_legend or None,
                "country": company.country_id.code or None,
                "street": company.street or None,
                "city": company.city or None,
                "zip": company.zip or None,
                "phone": company.phone or None,
                "email": company.email or None,
                "timezone": request.env.user.tz or "UTC",
            },
            "currency": {
                "id": currency.id,
                "name": currency.name,
                "symbol": currency.symbol,
                "decimal_places": currency.decimal_places,
                "rounding": currency.rounding,
            },
            "default_sale_tax_ids": company.account_sale_tax_id.ids,
            "price_include_default": company.account_price_include,
            "registers": [_register_payload(config) for config in registers],
        }
