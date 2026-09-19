import base64
import binascii
import math
import re

from odoo import _, api, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.fields import Domain

QUICK_CREATE_GROUP = "vlux_pos_catalog.group_vlux_catalog_quick_create"
BARCODE_RE = re.compile(r"^[A-Za-z0-9\-_.]{3,64}$")
NAME_MAX_LENGTH = 256
DEFAULT_CODE_MAX_LENGTH = 64
PRICE_MAX = 10_000_000.0
QTY_MAX = 1_000_000.0
IMAGE_MAX_BYTES = 6 * 1024 * 1024  # decoded; the client already downsizes to ~1024px JPEG
ACTIVE_SESSION_STATES = ("opening_control", "opened")


class ProductTemplate(models.Model):
    _inherit = "product.template"

    # ------------------------------------------------------------------
    # authorisation (server-side, never trust the POS client)
    # ------------------------------------------------------------------

    @api.model
    def _vlux_quick_create_config(self, config_id):
        """Return the pos.config the caller may quick-create for, or raise."""
        user = self.env.user
        if not user.has_group(QUICK_CREATE_GROUP):
            raise AccessError(_("Tu usuario no tiene permiso para registrar productos desde el POS."))
        if not user.has_group("point_of_sale.group_pos_user"):
            raise AccessError(_("El alta rapida requiere un usuario operador de POS."))
        try:
            config_id = int(config_id)
        except (TypeError, ValueError):
            raise UserError(_("Caja invalida."))
        # The regular (non-sudo) search applies the multi-company record rules
        # of the caller: a config of another company is simply not found.
        config = self.env["pos.config"].search([("id", "=", config_id)], limit=1)
        if not config:
            raise AccessError(_("La caja no existe o no pertenece a tu empresa."))
        session = self.env["pos.session"].search(
            [("config_id", "=", config.id), ("state", "in", ACTIVE_SESSION_STATES)], limit=1
        )
        if not session:
            raise UserError(_("La caja no tiene una sesion POS abierta."))
        return config

    # ------------------------------------------------------------------
    # validation
    # ------------------------------------------------------------------

    @api.model
    def _vlux_clean_barcode(self, value):
        barcode = str(value or "").strip()
        if not BARCODE_RE.match(barcode):
            raise ValidationError(_("El codigo de barras debe tener entre 3 y 64 caracteres alfanumericos."))
        return barcode

    @api.model
    def _vlux_clean_float(self, value, label, maximum, allow_zero=True):
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValidationError(_("%s no es un numero valido.", label))
        if not math.isfinite(number) or number < 0 or (number == 0 and not allow_zero) or number > maximum:
            raise ValidationError(_("%s esta fuera de rango.", label))
        return number

    @api.model
    def _vlux_clean_image(self, value):
        if not value:
            return False
        if not isinstance(value, str):
            raise ValidationError(_("La imagen no es valida."))
        payload = value.split(",", 1)[1] if value.startswith("data:") else value
        if len(payload) * 3 // 4 > IMAGE_MAX_BYTES:
            raise ValidationError(_("La imagen es demasiado grande."))
        try:
            raw = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError):
            raise ValidationError(_("La imagen no es valida."))
        if len(raw) > IMAGE_MAX_BYTES:
            raise ValidationError(_("La imagen es demasiado grande."))
        # fields.Image re-validates and re-encodes through Odoo's image tools;
        # a corrupt payload raises there as well, but we fail early with a clear message.
        try:
            from odoo.tools.image import ImageProcess

            ImageProcess(raw)
        except Exception:  # noqa: BLE001 - any decoding failure means "not an image"
            raise ValidationError(_("El archivo no es una imagen reconocida."))
        return base64.b64encode(raw)

    @api.model
    def _vlux_clean_many2one(self, model, value, domain, label):
        if not value:
            return False
        try:
            record_id = int(value)
        except (TypeError, ValueError):
            raise ValidationError(_("%s no es valido.", label))
        record = self.env[model].search(Domain("id", "=", record_id) & Domain(domain), limit=1)
        if not record:
            raise ValidationError(_("%s no existe o no esta disponible para esta empresa.", label))
        return record.id

    @api.model
    def _vlux_prepare_quick_create_values(self, values, config):
        if not isinstance(values, dict):
            raise ValidationError(_("Datos del producto invalidos."))
        company = config.company_id
        name = str(values.get("name") or "").strip()
        if not name:
            raise ValidationError(_("El nombre del producto es obligatorio."))
        if len(name) > NAME_MAX_LENGTH:
            raise ValidationError(_("El nombre del producto es demasiado largo."))
        barcode = self._vlux_clean_barcode(values.get("barcode"))
        list_price = self._vlux_clean_float(values.get("list_price"), _("El precio de venta"), PRICE_MAX)
        default_code = str(values.get("default_code") or "").strip()[:DEFAULT_CODE_MAX_LENGTH] or False
        is_storable = bool(values.get("is_storable", self._vlux_default_is_storable()))
        initial_qty = self._vlux_clean_float(values.get("initial_qty") or 0.0, _("El stock inicial"), QTY_MAX)

        company_domain = ["|", ("company_id", "=", False), ("company_id", "=", company.id)]
        pos_categ_id = self._vlux_clean_many2one(
            "pos.category", values.get("pos_categ_id"), [], _("La categoria POS")
        )
        categ_id = self._vlux_clean_many2one(
            "product.category", values.get("categ_id"), [], _("La categoria de producto")
        )
        tax_ids = []
        for raw_tax in values.get("taxes_ids") or []:
            tax_ids.append(
                self._vlux_clean_many2one(
                    "account.tax",
                    raw_tax,
                    [("type_tax_use", "=", "sale"), *company_domain],
                    _("El impuesto"),
                )
            )
        if "taxes_ids" not in values:
            tax_ids = company.account_sale_tax_id.ids

        product_values = {
            "name": name,
            "barcode": barcode,
            "list_price": list_price,
            "default_code": default_code,
            "type": "consu",
            "is_storable": is_storable,
            "available_in_pos": True,
            "sale_ok": True,
            "company_id": company.id,
            "taxes_id": [(6, 0, tax_ids)],
            "image_1920": self._vlux_clean_image(values.get("image")),
        }
        if pos_categ_id:
            product_values["pos_categ_ids"] = [(6, 0, [pos_categ_id])]
        if categ_id:
            product_values["categ_id"] = categ_id
        return product_values, initial_qty

    @api.model
    def _vlux_default_is_storable(self):
        value = self.env["ir.config_parameter"].sudo().get_param("vlux_pos_catalog.default_is_storable", "1")
        return value not in ("0", "false", "False", "")

    # ------------------------------------------------------------------
    # duplicate detection
    # ------------------------------------------------------------------

    @api.model
    def _vlux_lock_barcode(self, company, barcode):
        """Serialise concurrent quick-creates of the same barcode.

        Odoo enforces barcode uniqueness per company with a Python constraint,
        which leaves a race between two simultaneous creates. A transaction
        level advisory lock keyed on (company, barcode) closes that window
        without introducing a global UNIQUE index (which would break existing
        databases that already carry duplicates across companies).
        """
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            [f"vlux_pos_catalog:{company.id}:{barcode}"],
        )

    @api.model
    def _vlux_find_existing_by_barcode(self, company, barcode):
        Product = self.env["product.product"].sudo().with_context(active_test=False)
        return Product.search(
            [("barcode", "=", barcode), ("company_id", "in", [False, company.id])], limit=1
        )

    @api.model
    def _vlux_existing_payload(self, product):
        """Describe an existing product without leaking data the caller cannot read."""
        readable = product.with_env(self.env(su=False)).has_access("read")
        return {
            "id": product.id,
            "product_tmpl_id": product.product_tmpl_id.id,
            "name": product.display_name if readable else _("Producto existente"),
            "active": product.active,
            "available_in_pos": product.available_in_pos,
            "sale_ok": product.sale_ok,
        }

    # ------------------------------------------------------------------
    # public RPC surface (called from the POS with the user's session)
    # ------------------------------------------------------------------

    @api.model
    def vlux_pos_quick_create_defaults(self, config_id):
        config = self._vlux_quick_create_config(config_id)
        company = config.company_id
        return {
            "taxes_ids": company.account_sale_tax_id.ids,
            "is_storable": self._vlux_default_is_storable(),
            "pos_categ_id": (config.iface_available_categ_ids[:1].id if config.limit_categories else False),
            "currency_symbol": company.currency_id.symbol,
        }

    @api.model
    def vlux_pos_quick_create(self, values, config_id):
        """Create a sellable product from the POS quick form.

        Returns ``{"ok": True, "product_tmpl_id", "product_id", "barcode"}`` or
        ``{"ok": False, "code": "BARCODE_EXISTS", "existing": {...}}`` when the
        barcode is already assigned. Validation problems raise ValidationError.
        """
        config = self._vlux_quick_create_config(config_id)
        company = config.company_id
        product_values, initial_qty = self._vlux_prepare_quick_create_values(values, config)
        self._vlux_lock_barcode(company, product_values["barcode"])
        existing = self._vlux_find_existing_by_barcode(company, product_values["barcode"])
        if existing:
            return {"ok": False, "code": "BARCODE_EXISTS", "existing": self._vlux_existing_payload(existing)}
        packaging = self.env["product.uom"].sudo().search_count(
            [("barcode", "=", product_values["barcode"])], limit=1
        )
        if packaging:
            raise ValidationError(_("El codigo ya esta asignado a un empaque de producto."))

        # sudo only for the create itself: the caller was authorised above and
        # the values were whitelisted/validated, so this does not leak rights.
        template = (
            self.env["product.template"]
            .sudo()
            .with_company(company)
            .with_context(tracking_disable=True, mail_create_nolog=True)
            .create(product_values)
        )
        product = template.product_variant_id
        if initial_qty and template.is_storable:
            self._vlux_apply_initial_stock(config, product, initial_qty)
        return {
            "ok": True,
            "product_tmpl_id": template.id,
            "product_id": product.id,
            "barcode": product.barcode,
        }

    @api.model
    def vlux_pos_enable_existing(self, product_id, config_id):
        """Make an existing (possibly archived / non-POS) product sellable in the POS."""
        config = self._vlux_quick_create_config(config_id)
        try:
            product_id = int(product_id)
        except (TypeError, ValueError):
            raise UserError(_("Producto invalido."))
        product = (
            self.env["product.product"]
            .sudo()
            .with_context(active_test=False)
            .search([("id", "=", product_id), ("company_id", "in", [False, config.company_id.id])], limit=1)
        )
        if not product:
            raise UserError(_("El producto no existe o pertenece a otra empresa."))
        template = product.product_tmpl_id
        updates = {}
        if not template.active:
            updates["active"] = True
        if not template.available_in_pos:
            updates["available_in_pos"] = True
        if not template.sale_ok:
            updates["sale_ok"] = True
        if updates:
            template.with_context(tracking_disable=True).write(updates)
        if not product.active:
            product.write({"active": True})
        return {"ok": True, "product_tmpl_id": template.id, "product_id": product.id, "barcode": product.barcode}

    @api.model
    def _vlux_apply_initial_stock(self, config, product, quantity):
        location = config.picking_type_id.default_location_src_id
        if not location:
            warehouse = self.env["stock.warehouse"].sudo().search(
                [("company_id", "=", config.company_id.id)], limit=1
            )
            location = warehouse.lot_stock_id
        if not location:
            return
        quant = (
            self.env["stock.quant"]
            .sudo()
            .with_company(config.company_id)
            .with_context(inventory_mode=True)
            .create({
                "product_id": product.id,
                "location_id": location.id,
                "inventory_quantity": quantity,
            })
        )
        quant.action_apply_inventory()

    # ------------------------------------------------------------------
    # operational audit
    # ------------------------------------------------------------------

    @api.model
    def vlux_audit_duplicate_barcodes(self, company=None):
        """List barcodes assigned to more than one product (per company scope).

        Used by tests and diagnostics before considering any stricter
        uniqueness constraint. Read-only and safe to run on large catalogs:
        a single GROUP BY on the indexed barcode column.
        """
        company = company or self.env.company
        Product = self.env["product.product"].sudo().with_context(active_test=False)
        groups = Product._read_group(
            [("barcode", "!=", False), ("company_id", "in", [False, company.id])],
            ["barcode"],
            ["__count"],
            having=[("__count", ">", 1)],
        )
        return [{"barcode": barcode, "count": count} for barcode, count in groups]
