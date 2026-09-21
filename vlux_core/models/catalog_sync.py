"""Incremental catalog sync: a per-variant sync stamp and tombstones.

A VLUX register keeps a local copy of the catalog and asks the API for
"everything that changed since my cursor". Two things make that reliable:

* ``product.product.vlux_sync_date`` moves forward whenever the variant *or*
  its template is written. Odoo's own ``write_date`` is not enough: renaming or
  repricing a template does not touch its variants, and the register sells
  variants.
* ``vlux.catalog.tombstone`` remembers what was really deleted. Archiving or
  removing a product from the POS keeps the row (the client sees the flag);
  ``unlink`` does not, so without a tombstone a register that was offline
  comes back with ghost products it can still sell.

Tombstones are kept for ``TOMBSTONE_RETENTION_DAYS``; a cursor older than that
must resync from scratch.
"""
from datetime import timedelta

from odoo import api, fields, models
from odoo.tools import SQL

TOMBSTONE_RETENTION_DAYS = 90
SYNCED_MODELS = ("product.product", "res.partner")


class VluxCatalogTombstone(models.Model):
    _name = "vlux.catalog.tombstone"
    _description = "Baja definitiva del catálogo VLUX"
    _log_access = False

    model = fields.Char(required=True, readonly=True)
    res_id = fields.Integer(required=True, readonly=True)
    company_id = fields.Many2one("res.company", readonly=True, index=True)
    deleted_at = fields.Datetime(required=True, readonly=True, default=fields.Datetime.now)

    _model_res_unique = models.UniqueIndex("(model, res_id)")
    _deleted_at_idx = models.Index("(model, deleted_at, res_id)")

    @api.model
    def _record(self, records):
        """Remember ``records`` as deleted (idempotent), before they go."""
        if not records:
            return
        now = fields.Datetime.now()
        rows = [
            (records._name, record.id, (record.company_id.id or None) if "company_id" in record._fields else None, now)
            for record in records.with_context(active_test=False, prefetch_fields=False)
        ]
        self.env.cr.execute(SQL(
            "INSERT INTO vlux_catalog_tombstone (model, res_id, company_id, deleted_at) VALUES %s "
            "ON CONFLICT (model, res_id) DO NOTHING",
            SQL(", ").join(SQL("(%s, %s, %s, %s)", *row) for row in rows),
        ))

    @api.model
    def _between(self, model, company, after, until):
        """Ids of ``model`` deleted for ``company`` with ``(deleted_at, res_id)``
        in ``(after, until]``. Both bounds are ``(stamp, id)`` pairs; an
        ``until`` id of 0 means "strictly before that stamp"."""
        after_stamp, after_id = after
        until_stamp, until_id = until
        domain = [
            ("model", "=", model),
            "|", ("company_id", "=", False), ("company_id", "=", company.id),
            "|", ("deleted_at", ">", after_stamp),
            "&", ("deleted_at", "=", after_stamp), ("res_id", ">", after_id),
        ]
        if until_id:
            domain += ["|", ("deleted_at", "<", until_stamp),
                       "&", ("deleted_at", "=", until_stamp), ("res_id", "<=", until_id)]
        else:
            domain += [("deleted_at", "<", until_stamp)]
        return [row["res_id"] for row in self.sudo().search_read(domain, ["res_id"], order="deleted_at, res_id")]

    @api.autovacuum
    def _gc_old_tombstones(self):
        cutoff = fields.Datetime.now() - timedelta(days=TOMBSTONE_RETENTION_DAYS)
        self.sudo().search([("deleted_at", "<", cutoff)]).unlink()


class ProductProduct(models.Model):
    _inherit = "product.product"

    vlux_sync_date = fields.Datetime(
        string="Última modificación para sincronizar",
        default=fields.Datetime.now, readonly=True, copy=False, index=True,
        help="Avanza cuando cambia la variante o su plantilla; es el cursor de la API VLUX.",
    )

    _vlux_sync_cursor_idx = models.Index("(vlux_sync_date, id)")

    def init(self):
        # Rows that existed before the field did start from what Odoo knows.
        self.env.cr.execute("""
            UPDATE product_product pp
               SET vlux_sync_date = GREATEST(pp.write_date, pt.write_date, pp.create_date)
              FROM product_template pt
             WHERE pt.id = pp.product_tmpl_id AND pp.vlux_sync_date IS NULL
        """)

    @api.model
    def _vlux_touch_sync(self, domain_sql):
        """Bump the sync stamp with one statement; never through write()."""
        self.env.cr.execute(SQL(
            "UPDATE product_product SET vlux_sync_date = %s WHERE %s",
            fields.Datetime.now(), domain_sql,
        ))
        self.invalidate_model(["vlux_sync_date"])

    def write(self, vals):
        result = super().write(vals)
        if self and vals:
            self._vlux_touch_sync(SQL("id IN %s", tuple(self.ids)))
        return result

    def unlink(self):
        self.env["vlux.catalog.tombstone"]._record(self.exists())
        return super().unlink()


class ProductTemplate(models.Model):
    _inherit = "product.template"

    def write(self, vals):
        result = super().write(vals)
        if self and vals:
            self.env["product.product"]._vlux_touch_sync(SQL("product_tmpl_id IN %s", tuple(self.ids)))
        return result

    def unlink(self):
        # Deleting a template cascades to its variants at the SQL level, so
        # product.product.unlink() never sees them.
        variants = self.with_context(active_test=False).product_variant_ids
        self.env["vlux.catalog.tombstone"]._record(variants.exists())
        return super().unlink()


class ResPartner(models.Model):
    _inherit = "res.partner"

    def unlink(self):
        self.env["vlux.catalog.tombstone"]._record(self.exists())
        return super().unlink()
