"""Bulk catalog import: a store's spreadsheet into Odoo products.

Two steps, both server-side:

1. **Validate** reads the file, cleans every row, resolves taxes and
   categories against the company and matches each row to an existing
   product (by barcode, or by reference when there is no barcode). Nothing is
   written to the catalog; the result is a per-row error list and the counts
   of products to create and to update.
2. **Import** runs in the background (a cron worker, so no web request can
   time out) in batches of ``BATCH_SIZE`` rows, committing after each batch.
   Every row is an upsert keyed by barcode/reference, so a batch that is
   replayed after a crash, or the same file imported twice, never creates a
   duplicate. A batch that fails is retried row by row to isolate the bad
   row, which is recorded as an error; the rest go through.

Empty cells mean "leave as is" when updating, so a file with only
``codigo_barras`` and ``precio_venta`` is a price update.
"""
import base64
import hashlib
import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from . import catalog_import_parser as parser

_logger = logging.getLogger(__name__)

BATCH_SIZE = 500
IMPORT_GROUPS = ("vlux_core.group_vlux_administrator", "vlux_core.group_vlux_inventory_operator")
PRODUCT_FIELDS = ("name", "barcode", "default_code", "list_price", "standard_price", "is_storable", "available_in_pos")


def _norm(text):
    return parser.normalize(text)


class VluxCatalogImport(models.Model):
    _name = "vlux.catalog.import"
    _description = "Importación de catálogo VLUX"
    _order = "id desc"

    name = fields.Char(string="Archivo", required=True, default=lambda self: _("Importación"))
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, readonly=True)
    file = fields.Binary(string="Archivo (.xlsx o .csv)", attachment=True)
    file_checksum = fields.Char(readonly=True)
    create_missing_categories = fields.Boolean(
        string="Crear categorías que no existan", default=True,
        help="Si una categoría del archivo no existe, se crea. Si se desmarca, esa fila se reporta como error.",
    )
    location_id = fields.Many2one(
        "stock.location", string="Ubicación de existencias",
        domain="[('usage', '=', 'internal'), ('company_id', '=', company_id)]",
        default=lambda self: self._default_location(),
        help="Dónde se ajusta la columna existencia.",
    )
    state = fields.Selection(
        [("draft", "Borrador"), ("validated", "Validado"), ("queued", "En cola"),
         ("running", "Importando"), ("done", "Terminado"), ("failed", "Falló")],
        default="draft", required=True, readonly=True,
    )
    rows_total = fields.Integer(string="Filas", readonly=True)
    rows_valid = fields.Integer(string="Filas válidas", readonly=True)
    rows_to_create = fields.Integer(string="Por crear", readonly=True)
    rows_to_update = fields.Integer(string="Por actualizar", readonly=True)
    rows_done = fields.Integer(string="Procesadas", readonly=True)
    created_count = fields.Integer(string="Creados", readonly=True)
    updated_count = fields.Integer(string="Actualizados", readonly=True)
    unchanged_count = fields.Integer(string="Sin cambios", readonly=True,
                                     help="Productos del archivo que ya tenían exactamente esos datos.")
    stock_count = fields.Integer(string="Existencias ajustadas", readonly=True)
    error_count = fields.Integer(string="Errores", compute="_compute_error_count")
    progress = fields.Float(compute="_compute_progress")
    payload = fields.Json(readonly=True, copy=False)
    error_ids = fields.One2many("vlux.catalog.import.error", "import_id", readonly=True)
    started_at = fields.Datetime(readonly=True)
    finished_at = fields.Datetime(readonly=True)
    duration_seconds = fields.Float(string="Duración (s)", readonly=True)
    failure = fields.Text(readonly=True)

    @api.model
    def _default_location(self):
        warehouse = self.env["stock.warehouse"].search([("company_id", "=", self.env.company.id)], limit=1)
        return warehouse.lot_stock_id

    @api.depends("error_ids")
    def _compute_error_count(self):
        for record in self:
            record.error_count = len(record.error_ids)

    @api.depends("rows_done", "rows_valid")
    def _compute_progress(self):
        for record in self:
            record.progress = 100.0 * record.rows_done / record.rows_valid if record.rows_valid else 0.0

    # ------------------------------------------------------------------
    # rights
    # ------------------------------------------------------------------

    def _check_rights(self):
        if self.env.su:
            return
        user = self.env.user
        if not any(user.has_group(group) for group in IMPORT_GROUPS):
            raise AccessError(_("Sólo el administrador, el dueño o el operador de inventario pueden importar el catálogo."))

    @api.model_create_multi
    def create(self, vals_list):
        self._check_rights()
        return super().create(vals_list)

    def write(self, vals):
        if "file" in vals and any(record.state not in ("draft", "validated") for record in self):
            raise UserError(_("No se puede cambiar el archivo de una importación en curso o terminada."))
        if "file" in vals:
            vals = {**vals, "state": "draft", "payload": False}
        return super().write(vals)

    # ------------------------------------------------------------------
    # step 1: validate (read-only for the catalog)
    # ------------------------------------------------------------------

    def action_validate(self):
        self.ensure_one()
        self._check_rights()
        if self.state not in ("draft", "validated"):
            raise UserError(_("Esta importación ya se procesó."))
        if not self.file:
            raise UserError(_("Sube un archivo .xlsx o .csv."))
        data = base64.b64decode(self.file)
        self.error_ids.unlink()
        try:
            headers, rows = parser.read_table(data, self.name)
            parsed = parser.parse_rows(headers, rows)
        except parser.ParseError as error:
            self.write({"state": "draft", "payload": False, "rows_total": 0, "rows_valid": 0,
                        "rows_to_create": 0, "rows_to_update": 0})
            self.env["vlux.catalog.import.error"].create({"import_id": self.id, "row": 0, "message": str(error)})
            return self._reload()

        resolver = _Resolver(self.env, self.company_id, create_missing=self.create_missing_categories)
        errors, valid = [], []
        for item in parsed:
            row_errors = list(item["errors"])
            resolved = {}
            if not row_errors:
                resolved, row_errors = resolver.resolve(item["values"])
            if row_errors:
                errors += [{"import_id": self.id, "row": item["row"], "column": column, "message": message}
                           for column, message in row_errors]
            else:
                valid.append({"row": item["row"], "values": resolved})

        matches = _match_existing(self.env, self.company_id, [row["values"] for row in valid])
        to_create = to_update = 0
        for row in valid:
            existing = matches.get(_key(row["values"]))
            if existing is None:
                missing = [label for field, label in (("name", "nombre"), ("list_price", "precio_venta"))
                           if field not in row["values"]]
                if missing:
                    errors.append({"import_id": self.id, "row": row["row"], "column": missing[0],
                                   "message": _("Producto nuevo: falta %s.", ", ".join(missing))})
                    row["skip"] = True
                    continue
                to_create += 1
            elif existing is False:
                errors.append({"import_id": self.id, "row": row["row"], "column": "codigo_barras",
                               "message": _("Ese código pertenece a un producto de otra empresa.")})
                row["skip"] = True
            else:
                to_update += 1
        valid = [row for row in valid if not row.get("skip")]
        if errors:
            self.env["vlux.catalog.import.error"].create(errors)
        self.write({
            "state": "validated",
            "payload": {"rows": valid},
            "file_checksum": hashlib.sha256(data).hexdigest(),
            "rows_total": len(parsed),
            "rows_valid": len(valid),
            "rows_to_create": to_create,
            "rows_to_update": to_update,
            "rows_done": 0,
        })
        return self._reload()

    # ------------------------------------------------------------------
    # step 2: import in the background
    # ------------------------------------------------------------------

    def action_import(self):
        self.ensure_one()
        self._check_rights()
        if self.state != "validated":
            raise UserError(_("Primero valida el archivo."))
        if not self.rows_valid:
            raise UserError(_("No hay filas válidas para importar."))
        self.write({"state": "queued", "rows_done": 0, "created_count": 0, "updated_count": 0,
                    "unchanged_count": 0, "stock_count": 0, "failure": False,
                    "started_at": False, "finished_at": False})
        self.env.ref("vlux_pos_catalog.ir_cron_vlux_catalog_import")._trigger()
        return self._reload()

    def action_refresh(self):
        return self._reload()

    def _reload(self):
        return {"type": "ir.actions.act_window", "res_model": self._name, "res_id": self.id,
                "view_mode": "form", "target": "current"}

    @api.model
    def _cron_process(self):
        """Process queued imports, one batch at a time, committing progress."""
        Cron = self.env["ir.cron"]
        while True:
            job = self.search([("state", "in", ("queued", "running"))], order="id", limit=1)
            if not job:
                return
            before = job.rows_done
            finished = job._run_batch()
            remaining = 0 if finished else max(job.rows_valid - job.rows_done, 1)
            # Commits the batch; returns the time left for this cron run.
            if Cron._commit_progress(processed=job.rows_done - before, remaining=remaining) <= 0:
                return

    def _run_batch(self):
        """Apply the next ``BATCH_SIZE`` rows. Returns True when the import is finished."""
        self.ensure_one()
        now = fields.Datetime.now()
        if self.state == "queued":
            self.write({"state": "running", "started_at": now})
        rows = (self.payload or {}).get("rows", [])
        batch = rows[self.rows_done:self.rows_done + BATCH_SIZE]
        env = self.env(su=True)
        company = self.company_id
        importer = _Importer(env, company, self.location_id, self.create_missing_categories)
        try:
            with env.cr.savepoint():
                stats, row_errors = importer.apply(batch)
        except Exception:  # noqa: BLE001 - isolate the failing row(s)
            _logger.info("VLUX catalog import %s: batch failed, retrying row by row", self.id, exc_info=True)
            stats, row_errors = {"created": 0, "updated": 0, "unchanged": 0, "stock": 0}, []
            for row in batch:
                try:
                    with env.cr.savepoint():
                        one, errs = importer.apply([row])
                    for key in stats:
                        stats[key] += one[key]
                    row_errors += errs
                except Exception as error:  # noqa: BLE001
                    message = error.args[0] if getattr(error, "args", None) else str(error)
                    row_errors.append({"row": row["row"], "column": "", "message": str(message)[:500]})
        if row_errors:
            self.env["vlux.catalog.import.error"].create([{**error, "import_id": self.id} for error in row_errors])
        values = {
            "rows_done": self.rows_done + len(batch),
            "created_count": self.created_count + stats["created"],
            "updated_count": self.updated_count + stats["updated"],
            "unchanged_count": self.unchanged_count + stats["unchanged"],
            "stock_count": self.stock_count + stats["stock"],
        }
        finished = values["rows_done"] >= len(rows)
        if finished:
            end = fields.Datetime.now()
            values.update({"state": "done", "finished_at": end,
                           "duration_seconds": (end - (self.started_at or now)).total_seconds()})
        self.write(values)
        env.invalidate_all()
        return finished

    def _run_to_completion(self):
        """Synchronous path (tests, shell, benchmarks): every batch, no commits."""
        self.ensure_one()
        while self.state in ("queued", "running"):
            self._run_batch()
        return self


class VluxCatalogImportError(models.Model):
    _name = "vlux.catalog.import.error"
    _description = "Error de importación de catálogo VLUX"
    _order = "row, id"

    import_id = fields.Many2one("vlux.catalog.import", required=True, ondelete="cascade", index=True)
    row = fields.Integer(string="Fila", help="Fila de la hoja (el encabezado es la fila 1); 0 = el archivo completo.")
    column = fields.Char(string="Columna")
    message = fields.Char(string="Problema", required=True)


# ----------------------------------------------------------------------
# helpers (not models)
# ----------------------------------------------------------------------


def _key(values):
    if values.get("barcode"):
        return ("barcode", values["barcode"])
    return ("default_code", values["default_code"])


def _match_existing(env, company, rows):
    """``{key: product id | False}``; False = the key belongs to another company.

    Two queries whatever the file size.
    """
    Product = env["product.product"].sudo().with_context(active_test=False)
    result = {}
    barcodes = [row["barcode"] for row in rows if row.get("barcode")]
    references = [row["default_code"] for row in rows if not row.get("barcode") and row.get("default_code")]
    for field, keys in (("barcode", barcodes), ("default_code", references)):
        if not keys:
            continue
        for record in Product.search_read([(field, "in", keys)], [field, "company_id"], order="id"):
            key = (field, record[field])
            own = not record["company_id"] or record["company_id"][0] == company.id
            if own:
                if not result.get(key):
                    result[key] = record["id"]
            else:
                result.setdefault(key, False)
    return result


class _Resolver:
    """Turns the text cells that name records (taxes, categories) into ids."""

    def __init__(self, env, company, create_missing):
        self.env = env
        self.company = company
        self.create_missing = create_missing
        taxes = env["account.tax"].sudo().search([
            ("type_tax_use", "=", "sale"), ("company_id", "=", company.id), ("active", "=", True),
        ])
        self.taxes = [(tax.id, _norm(tax.name), tax.amount, tax.amount_type) for tax in taxes]
        self.pos_categories = {}
        for category in env["pos.category"].sudo().search([]):
            self.pos_categories.setdefault(_norm(category.name), category.id)
            self.pos_categories.setdefault(_norm(_path(category)), category.id)
        self.categories = {}
        for category in env["product.category"].sudo().search([]):
            self.categories.setdefault(_norm(category.complete_name), category.id)
            self.categories.setdefault(_norm(category.name), category.id)

    def resolve(self, values):
        resolved, errors = dict(values), []
        if "taxes" in values:
            ids, error = self._taxes(values["taxes"])
            del resolved["taxes"]
            if error:
                errors.append(("impuestos", error))
            else:
                resolved["tax_ids"] = ids
        for column, label, table in (("pos_category", "categoria_pos", self.pos_categories),
                                     ("category", "categoria", self.categories)):
            if column not in values:
                continue
            found = table.get(_norm(values[column]))
            if found:
                resolved[column] = found
            elif self.create_missing:
                resolved[column] = values[column].strip()  # created at import time
            else:
                errors.append((label, _("La categoría «%s» no existe.", values[column])))
        return resolved, errors

    def _taxes(self, text):
        if _norm(text) in ("ninguno", "sin_impuesto", "none", "0_sin_impuesto"):
            return [], None
        ids = []
        for part in [p for p in text.replace("+", ";").split(";") if p.strip()]:
            key = _norm(part)
            exact = [tax_id for tax_id, name, _amount, _type in self.taxes if name == key]
            if len(exact) == 1:
                ids.append(exact[0])
                continue
            contains = [tax_id for tax_id, name, _amount, _type in self.taxes if key and key in name]
            if len(contains) == 1:
                ids.append(contains[0])
                continue
            try:
                amount = parser.parse_number(part.replace("%", "").replace("IVA", "").replace("iva", ""))
            except (TypeError, ValueError):
                amount = None
            by_amount = [tax_id for tax_id, _name, tax_amount, kind in self.taxes
                         if amount is not None and kind == "percent" and abs(tax_amount - amount) < 1e-6]
            if len(by_amount) == 1:
                ids.append(by_amount[0])
                continue
            candidates = exact or contains or by_amount
            if candidates:
                names = ", ".join(self.env["account.tax"].sudo().browse(candidates).mapped("name"))
                return None, _("«%s» es ambiguo: %s. Escribe el nombre exacto (hoja Impuestos de la plantilla).", part.strip(), names)
            return None, _("No existe el impuesto «%s» (ver hoja Impuestos de la plantilla).", part.strip())
        return ids, None


def _path(category):
    names = []
    while category:
        names.insert(0, category.name)
        category = category.parent_id
    return " / ".join(names)


class _Importer:
    """Applies validated rows; every row is an idempotent upsert."""

    def __init__(self, env, company, location, create_missing_categories):
        self.env = env
        self.company = company
        self.location = location
        self.create_missing = create_missing_categories
        self.Template = env["product.template"].with_company(company).with_context(
            tracking_disable=True, mail_create_nolog=True, mail_notrack=True,
        )
        self.default_taxes = company.account_sale_tax_id.ids

    def _category(self, model, value):
        if isinstance(value, int):
            return value
        Model = self.env[model]
        parent = Model.browse()
        for name in [part.strip() for part in value.split("/") if part.strip()]:
            found = Model.search([("name", "=ilike", name), ("parent_id", "=", parent.id or False)], limit=1)
            if not found:
                if not self.create_missing:
                    raise UserError(_("La categoría «%s» no existe.", value))
                found = Model.create({"name": name, "parent_id": parent.id or False})
            parent = found
        return parent.id

    def _template_values(self, values, creating):
        vals = {field: values[field] for field in PRODUCT_FIELDS if field in values}
        if "tax_ids" in values:
            vals["taxes_id"] = [(6, 0, values["tax_ids"])]
        elif creating:
            vals["taxes_id"] = [(6, 0, self.default_taxes)]
        if "pos_category" in values:
            vals["pos_categ_ids"] = [(6, 0, [self._category("pos.category", values["pos_category"])])]
        if "category" in values:
            vals["categ_id"] = self._category("product.category", values["category"])
        if creating:
            vals.setdefault("available_in_pos", True)
            vals.setdefault("is_storable", "quantity" in values)
            vals.update({"type": "consu", "sale_ok": True, "company_id": self.company.id})
        return vals

    @staticmethod
    def _changes(record, vals):
        """Drop from ``vals`` what ``record`` already has: a no-op write still
        costs a full ORM write (recomputes, variant sync, sync stamp)."""
        changed = {}
        for field, value in vals.items():
            current = record[field]
            if isinstance(value, list) and value and isinstance(value[0], tuple):  # (6, 0, ids)
                if set(current.ids) != set(value[0][2]):
                    changed[field] = value
            elif hasattr(current, "_name"):  # many2one
                if current.id != value:
                    changed[field] = value
            elif isinstance(value, float) or isinstance(current, float):
                if abs((current or 0.0) - (value or 0.0)) > 1e-9:
                    changed[field] = value
            elif current != value:
                changed[field] = value
        return changed

    def apply(self, rows):
        stats = {"created": 0, "updated": 0, "unchanged": 0, "stock": 0}
        errors = []
        matches = _match_existing(self.env, self.company, [row["values"] for row in rows])
        to_create, to_update = [], []
        for row in rows:
            existing = matches.get(_key(row["values"]))
            if existing is False:
                errors.append({"row": row["row"], "column": "codigo_barras",
                               "message": _("Ese código pertenece a un producto de otra empresa.")})
            elif existing:
                to_update.append((row, existing))
            else:
                to_create.append(row)
        products = {}
        if to_create:
            templates = self.Template.create([self._template_values(row["values"], True) for row in to_create])
            for row, template in zip(to_create, templates):
                products[row["row"]] = template.product_variant_id
            stats["created"] = len(templates)
        Product = self.env["product.product"].with_company(self.company).with_context(active_test=False)
        # One prefetch for the whole batch instead of a read per row.
        batch_products = Product.browse([product_id for _row, product_id in to_update])
        batch_products.read(["barcode", "default_code", "active"])
        batch_products.product_tmpl_id.read(["name", "list_price", "standard_price", "is_storable",
                                             "available_in_pos", "taxes_id", "pos_categ_ids", "categ_id", "active"])
        for row, product_id in to_update:
            product = Product.browse(product_id)
            template = product.product_tmpl_id
            vals = self._template_values(row["values"], False)
            barcode = vals.pop("barcode", None)
            code = vals.pop("default_code", None)
            template_vals = self._changes(template, vals)
            if not template.active or not product.active:
                template_vals["active"] = True
            variant_vals = {k: v for k, v in (("barcode", barcode), ("default_code", code)) if v and product[k] != v}
            if template_vals:
                template.with_context(tracking_disable=True, mail_notrack=True).write(template_vals)
            if variant_vals:
                product.write(variant_vals)
            products[row["row"]] = product
            stats["updated" if template_vals or variant_vals else "unchanged"] += 1
        stock_rows = [(products[row["row"]], row["values"]["quantity"]) for row in rows
                      if "quantity" in row["values"] and row["row"] in products]
        if stock_rows and self.location:
            stats["stock"] = self._set_quantities(stock_rows)
        return stats, errors

    def _set_quantities(self, stock_rows):
        """Set on-hand quantities at the location; rows already right are skipped
        (an inventory adjustment writes a stock move and its valuation)."""
        Quant = self.env["stock.quant"].with_company(self.company).with_context(inventory_mode=True)
        current = dict(Quant._read_group(
            [("product_id", "in", [product.id for product, _qty in stock_rows]),
             ("location_id", "=", self.location.id)],
            ["product_id"], ["quantity:sum"],
        ))
        current = {product.id: quantity for product, quantity in current.items()}
        stock_rows = [(product, quantity) for product, quantity in stock_rows
                      if abs(current.get(product.id, 0.0) - quantity) > 1e-9 or not product.is_storable]
        quants = Quant.browse()
        for product, quantity in stock_rows:
            if not product.is_storable:
                product.product_tmpl_id.write({"is_storable": True})
            quants |= Quant.create({
                "product_id": product.id,
                "location_id": self.location.id,
                "inventory_quantity": quantity,
            })
        quants = quants.filtered(lambda quant: quant.inventory_quantity_set)
        if quants:
            quants.action_apply_inventory()
        return len(stock_rows)
