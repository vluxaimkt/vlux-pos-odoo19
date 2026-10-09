"""What a VLUX register asks of its ``pos.config``: who may work it and what a cart costs.

Prices and taxes are computed here, on the server, with the same engine
``pos.order._compute_prices`` uses, so the total a customer is charged is the
total Odoo books. A client may send the unit price it charged (an offline
register prices from its local copy of the catalog); a price that differs
from the catalog is accepted and reported, never silently replaced.
"""
import base64
import binascii

from odoo import SUPERUSER_ID, _, fields, models
from odoo.exceptions import ValidationError
from odoo.fields import Domain
from odoo.tools import float_compare, plaintext2html
from odoo.tools.image import ImageProcess

# Lines per order: a guard against a runaway client, far above any real ticket.
MAX_LINES = 500
# Access to a register (pos_hr lists) the register's employees module can set.
STAFF_ROLES = ("manager", "cashier", "minimal", "none")
OWNER_GROUPS = ("vlux_core.group_vlux_owner", "vlux_owner.group_vlux_owner")
# A product picture as sent by a register (it downsizes to ~1024 px first).
IMAGE_MAX_BYTES = 6 * 1024 * 1024
# vlux_pos_catalog's permission to quick-create products (optional module).
QUICK_CREATE_GROUP = "vlux_pos_catalog.group_vlux_catalog_quick_create"


class PosConfig(models.Model):
    _inherit = "pos.config"

    # Store options of the VLUX register: each business decides; defaults are Odoo's behaviour.
    vlux_cash_in_reasons = fields.Text(
        string="Motivos rápidos de entrada de efectivo",
        default="Cambio (morralla)\nFondo adicional",
        help="Uno por renglón: botones de motivo al meter efectivo en la caja VLUX.",
    )
    vlux_cash_out_reasons = fields.Text(
        string="Motivos rápidos de salida de efectivo",
        default="Pago a proveedor\nRetiro del dueño\nGasto de la tienda",
        help="Uno por renglón: botones de motivo al sacar efectivo en la caja VLUX.",
    )
    vlux_catalog_editors = fields.Selection(
        [("managers", "Encargados y dueño"), ("owner", "Sólo el dueño")],
        string="Quién da de alta y edita productos desde la caja",
        default="managers", required=True,
        help="Alta rápida de productos desconocidos y edición (precio, descripción, foto…) desde la caja VLUX.",
    )
    vlux_staff_admins = fields.Selection(
        [("owner", "Sólo el dueño"), ("managers", "Encargados y dueño")],
        string="Quién administra empleados desde la caja",
        default="owner", required=True,
        help="Quién puede dar de alta empleados, cambiar su acceso y su PIN desde la caja VLUX.",
    )
    vlux_refunds_need_manager = fields.Boolean(
        string="Devoluciones requieren autorización del encargado",
        default=True,
        help="En la caja VLUX, una devolución hecha por un cajero necesita el PIN de un encargado (o del dueño). "
             "Los encargados devuelven directo.",
    )
    vlux_cashier_cash_out = fields.Boolean(
        string="Cajeros pueden sacar efectivo",
        help="Permite a los cajeros registrar salidas de efectivo (p. ej. pagar a un proveedor) con motivo y a su "
             "nombre. Las entradas siguen siendo del encargado.",
    )

    # --- employees (pos_hr) -------------------------------------------------

    def _vlux_api_employee_records(self):
        self.ensure_one()
        # sudo: a cashier cannot read hr.employee, yet the Odoo POS loads the
        # same list for them. The domain keeps it to this register's company
        # and allowed employees.
        return self.env["hr.employee"].sudo().search(self._employee_domain(self.env.uid), order="name, id")

    def _vlux_api_register_options(self):
        """Store options the register screen adapts to (extension point).

        Each module adds its own keys (``super()`` then update), so a new
        option never needs a change here or in the screens that ignore it.
        """
        self.ensure_one()

        def lines(text):
            return [line.strip()[:60] for line in (text or "").splitlines() if line.strip()][:12]

        return {
            "cashier_cash_out": bool(self.vlux_cashier_cash_out),
            "refunds_need_manager": bool(self.vlux_refunds_need_manager),
            "cash_reasons": {"in": lines(self.vlux_cash_in_reasons), "out": lines(self.vlux_cash_out_reasons)},
        }

    def _vlux_api_employees(self):
        """Employees who may work this register, as pos_hr hands them to the Odoo POS.

        ``pin_sha1``/``barcode_sha1`` let the register check a PIN or a badge
        without the network, exactly like the Odoo POS does. A 4-digit PIN
        hashed without salt can be brute-forced, so these hashes only go to a
        token that may already sell on this register.
        """
        self.ensure_one()
        if not self.module_pos_hr:
            return []
        employees = self._vlux_api_employee_records()
        rows = self.env["hr.employee"]._load_pos_data_read(employees, self)
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "role": row.get("_role") or "cashier",
                "user_id": row.get("user_id") or None,
                "pin_sha1": row.get("_pin") or None,
                "barcode_sha1": row.get("_barcode") or None,
                "can_manage_staff": self._vlux_api_can_manage_staff(employees.browse(row["id"])),
                "can_edit_catalog": self._vlux_api_can_edit_catalog(employees.browse(row["id"])),
                "is_owner": self._vlux_api_is_owner(employees.browse(row["id"])),
            }
            for row in rows
        ]

    # --- owner module ----------------------------------------------------------

    def _vlux_api_owner_sections(self):
        """Sections of the owner module this store has (extension point: modules add theirs)."""
        self.ensure_one()
        sections = ["options", "authorizations"]
        if "vlux.owner.dashboard.service" in self.env:
            sections.insert(0, "dashboard")
        return sections

    def _vlux_api_option_fields(self):
        """Register options the owner may change from the register (extension point)."""
        return ["vlux_refunds_need_manager", "vlux_cashier_cash_out", "vlux_cash_in_reasons", "vlux_cash_out_reasons",
                "vlux_catalog_editors", "vlux_staff_admins"]

    def _vlux_api_options_form(self):
        """The options as a form the register can draw: label, help, type, choices and value."""
        self.ensure_one()
        config = self.sudo()
        rows = []
        for name in self._vlux_api_option_fields():
            field = config._fields[name]
            value = config[name]
            rows.append({
                "name": name,
                "label": field.string,
                "help": field.help or "",
                "type": field.type,
                "choices": [{"value": key, "label": label} for key, label in field.selection] if field.type == "selection" else None,
                "value": value if value is not False or field.type == "boolean" else "",
            })
        return rows

    def _vlux_api_set_options(self, owner, values):
        """Change register options from the register (only the published ones), as the owner's own user."""
        self.ensure_one()
        allowed = set(self._vlux_api_option_fields())
        unknown = set(values) - allowed
        if unknown:
            raise ValidationError(_("Opción desconocida: %s", ", ".join(sorted(unknown))))
        vals = {}
        for name, value in values.items():
            field = self._fields[name]
            if field.type == "boolean":
                vals[name] = bool(value)
            elif field.type == "selection":
                if value not in dict(field.selection):
                    raise ValidationError(_("Valor inválido para %s.", field.string))
                vals[name] = value
            else:
                vals[name] = str(value or "")[:2000]
        owner_user = owner.sudo().user_id
        self.with_user(owner_user or SUPERUSER_ID).sudo().write(vals)
        return self._vlux_api_options_form()

    # --- staff (employees module of the register) ----------------------------

    def _vlux_api_is_owner(self, employee):
        user = employee.sudo().user_id if employee else self.env["res.users"]
        for xmlid in OWNER_GROUPS:
            group = self.env.ref(xmlid, raise_if_not_found=False)
            if user and group and group in user.all_group_ids:
                return True
        return False

    def _vlux_api_can_manage_staff(self, employee):
        """Who may manage employees from the register: the register's option (extension point)."""
        self.ensure_one()
        if not employee:
            return False
        if self._vlux_api_is_owner(employee):
            return True
        return self.vlux_staff_admins == "managers" and self._vlux_api_is_manager(employee)

    def _vlux_api_can_edit_catalog(self, employee):
        """Who may add and edit products from the register: the register's option (extension point).

        The owner always; managers unless the store keeps it to the owner; or
        an employee whose Odoo user has the POS quick-create group.
        """
        self.ensure_one()
        if not employee:
            return False
        if self._vlux_api_is_owner(employee):
            return True
        if self.vlux_catalog_editors == "managers" and self._vlux_api_is_manager(employee):
            return True
        return self._vlux_api_user_in(employee.sudo().user_id, QUICK_CREATE_GROUP)

    def _vlux_api_user_in(self, user, xmlid):
        """Whether ``user`` has the group ``xmlid``; False when its module is not installed."""
        group = self.env.ref(xmlid, raise_if_not_found=False)
        return bool(user and group and group in user.all_group_ids)

    def _vlux_api_staff_role(self, employee):
        """The employee's access to this register, as pos_hr decides it."""
        config = self.sudo()
        employee = employee.sudo()
        if not employee.active:
            return "none"
        if employee.user_id and employee.user_id.has_group("point_of_sale.group_pos_manager"):
            return "manager"
        if employee in config.advanced_employee_ids:
            return "manager"
        if employee in config.minimal_employee_ids:
            return "minimal"
        if employee in config.basic_employee_ids or not config.basic_employee_ids:
            # pos_hr: an empty cashier list lets every employee of the company in.
            return "cashier"
        return "none"

    def _vlux_api_staff(self):
        """Every employee of the register's company with their access to this register."""
        self.ensure_one()
        employees = self.env["hr.employee"].sudo().with_context(active_test=False).search(
            [("company_id", "in", [False, self.company_id.id])], order="active desc, name, id",
        )
        return [
            {
                "id": employee.id,
                "name": employee.name,
                "role": self._vlux_api_staff_role(employee),
                "active": employee.active,
                "has_pin": bool(employee.pin),
                # A POS manager user is a manager everywhere: that is changed in Odoo, not here.
                "odoo_manager": bool(employee.user_id and employee.user_id.has_group("point_of_sale.group_pos_manager")),
                "owner": self._vlux_api_is_owner(employee),
                "user": employee.user_id.name or None,
            }
            for employee in employees
        ]

    @staticmethod
    def _vlux_api_clean_pin(pin):
        pin = str(pin or "").strip()
        if not pin.isdigit() or not 4 <= len(pin) <= 8:
            raise ValidationError(_("El PIN debe tener de 4 a 8 números."))
        return pin

    def _vlux_api_set_staff(self, employee, actor, name=None, role=None, pin=None, active=None):
        """Change an employee's name, access to this register, PIN, or archive them.

        Nobody is deleted: leaving the store archives the employee. The person
        making the change cannot lower their own access or archive
        themselves, and a POS manager user stays a manager (Odoo decides it).
        """
        self.ensure_one()
        # The PIN of whoever the register's option allows authorised this, not the
        # device's (cashier) user: write as the actor's own user (audit trail), or
        # the system when the actor has none. Private: not reachable over RPC.
        actor_user = actor.sudo().user_id
        config = self.with_user(actor_user or SUPERUSER_ID).sudo()
        employee = employee.sudo()
        if employee == actor and (role not in (None, "manager") or active is False):
            raise ValidationError(_("No puedes quitarte tu propio acceso."))
        if name is not None:
            name = str(name).strip()
            if not name or len(name) > 100:
                raise ValidationError(_("Escribe el nombre del empleado (hasta 100 letras)."))
            employee.name = name
        if pin is not None:
            employee.pin = self._vlux_api_clean_pin(pin)
        if active is False:
            role = "none"
        if role is not None:
            if role not in STAFF_ROLES:
                raise ValidationError(_("Acceso inválido."))
            odoo_manager = employee.user_id and employee.user_id.has_group("point_of_sale.group_pos_manager")
            if odoo_manager and role != "manager":
                raise ValidationError(_("%s es gerente del POS en Odoo: cámbialo desde Odoo.", employee.name))
            if not config.basic_employee_ids:
                # Make pos_hr's "everyone is a cashier" explicit before restricting anyone.
                implicit = self.env["hr.employee"].sudo().search([
                    ("company_id", "in", [False, config.company_id.id]),
                    ("id", "not in", (config.advanced_employee_ids | config.minimal_employee_ids).ids),
                ])
                config.basic_employee_ids = [fields.Command.set(implicit.ids)]
            commands = {
                field: [fields.Command.unlink(employee.id)]
                for field in ("basic_employee_ids", "advanced_employee_ids", "minimal_employee_ids")
            }
            target = {"manager": "advanced_employee_ids", "cashier": "basic_employee_ids",
                      "minimal": "minimal_employee_ids"}.get(role)
            if target:
                commands[target] = [fields.Command.link(employee.id)]
            config.write(commands)
            if not config.basic_employee_ids:
                # Odoo reads an empty cashier list as "everyone": keep the restriction real.
                raise ValidationError(_(
                    "Debe quedar al menos un cajero en esta caja: en Odoo una caja sin cajeros deja entrar a todos."))
        if active is not None:
            employee.active = bool(active)
        return employee

    # --- catalog from the register ---------------------------------------------

    @staticmethod
    def _vlux_api_clean_image(value):
        """A picture sent by the register (base64 or data URI), checked to be an image."""
        if not value:
            return False
        if not isinstance(value, str):
            raise ValidationError(_("La imagen no es válida."))
        payload = value.split(",", 1)[1] if value.startswith("data:") else value
        try:
            raw = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError):
            raise ValidationError(_("La imagen no es válida."))
        if len(raw) > IMAGE_MAX_BYTES:
            raise ValidationError(_("La imagen es demasiado grande."))
        try:
            ImageProcess(raw)
        except Exception:  # noqa: BLE001 - any decoding failure means "not an image"
            raise ValidationError(_("El archivo no es una imagen reconocida."))
        return base64.b64encode(raw)

    def _vlux_api_update_product(self, product, actor, values):
        """Change a product from the register: name, price, purchase price,
        description, barcode, POS category, sold by weight, sale tax and
        picture. Written as the actor's own user (audit), or the system when
        the actor has none."""
        self.ensure_one()
        actor_user = actor.sudo().user_id if actor else self.env["res.users"]
        env_user = actor_user or self.env.ref("base.user_root")
        template = product.product_tmpl_id.with_user(env_user).sudo().with_company(self.company_id)
        vals = {}
        if "name" in values:
            name = str(values["name"] or "").strip()
            if not name or len(name) > 120:
                raise ValidationError(_("Escribe el nombre del producto (hasta 120 letras)."))
            vals["name"] = name
        if "list_price" in values:
            try:
                price = float(values["list_price"])
            except (TypeError, ValueError):
                raise ValidationError(_("El precio debe ser un número."))
            if price < 0 or price > 10_000_000:
                raise ValidationError(_("El precio no es válido."))
            # The register shows the variant's price (template price + attribute extras).
            extra = sum(product.product_template_attribute_value_ids.mapped("price_extra"))
            vals["list_price"] = price - extra
        if "standard_price" in values:
            try:
                cost = float(values["standard_price"] or 0)
            except (TypeError, ValueError):
                raise ValidationError(_("El precio de compra debe ser un número."))
            if cost < 0 or cost > 10_000_000:
                raise ValidationError(_("El precio de compra no es válido."))
            # The cost is per variant and per company.
            product.with_user(env_user).sudo().with_company(self.company_id).standard_price = cost
        if "description" in values:
            text = str(values["description"] or "").strip()[:2000]
            vals["public_description"] = plaintext2html(text) if text else False
        if "to_weight" in values:
            vals["to_weight"] = bool(values["to_weight"])
        if "pos_categ_id" in values:
            categ_id = values["pos_categ_id"]
            if categ_id:
                category = self.env["pos.category"].sudo().search([("id", "=", int(categ_id))], limit=1)
                if not category:
                    raise ValidationError(_("La categoría no existe."))
                vals["pos_categ_ids"] = [fields.Command.set(category.ids)]
            else:
                vals["pos_categ_ids"] = [fields.Command.clear()]
        if "taxes_ids" in values:
            taxes = self.env["account.tax"].sudo().search([
                ("id", "in", [int(tax) for tax in values["taxes_ids"] or []]),
                ("type_tax_use", "=", "sale"), ("company_id", "in", [False, self.company_id.id]),
            ])
            vals["taxes_id"] = [fields.Command.set(taxes.ids)]
        if "image" in values:
            vals["image_1920"] = self._vlux_api_clean_image(values["image"])
        if "barcode" in values:
            barcode = str(values["barcode"] or "").strip() or False
            if barcode:
                taken = self.env["product.product"].sudo().with_context(active_test=False).search([
                    ("barcode", "=", barcode), ("id", "!=", product.id),
                    ("company_id", "in", [False, self.company_id.id]),
                ], limit=1)
                if taken:
                    raise ValidationError(_("El código %s ya es de %s.", barcode, taken.display_name))
            # The barcode is the variant's own.
            product.with_user(env_user).sudo().barcode = barcode
        if vals:
            template.write(vals)
        return product

    def _vlux_api_add_staff(self, actor, name, role, pin):
        self.ensure_one()
        name = str(name or "").strip()
        if not name or len(name) > 100:
            raise ValidationError(_("Escribe el nombre del empleado (hasta 100 letras)."))
        employee = self.env["hr.employee"].sudo().create({
            "name": name, "company_id": self.company_id.id, "pin": self._vlux_api_clean_pin(pin),
        })
        self._vlux_api_set_staff(employee, actor, role=role or "cashier")
        return employee

    def _vlux_api_employee(self, employee_id):
        """The employee working the register, required when it uses employee login."""
        self.ensure_one()
        Employee = self.env["hr.employee"]
        if not self.module_pos_hr:
            return Employee
        if not employee_id:
            raise ValidationError(_("Esta caja trabaja con empleados: falta employee_id."))
        try:
            employee_id = int(employee_id)
        except (TypeError, ValueError):
            raise ValidationError(_("employee_id debe ser un entero."))
        employee = Employee.sudo().search(
            Domain.AND([self._employee_domain(self.env.uid), [("id", "=", employee_id)]]), limit=1,
        )
        if not employee:
            raise ValidationError(_("El empleado %s no puede usar esta caja.", employee_id))
        return employee.sudo(False)

    def _vlux_api_is_manager(self, employee):
        """Manager on this register, as pos_hr decides it (POS manager user or advanced access)."""
        self.ensure_one()
        if not employee:
            return False
        user = employee.sudo().user_id
        return bool(
            (user and user.has_group("point_of_sale.group_pos_manager"))
            or employee in self.advanced_employee_ids
        )

    def _vlux_api_check_closer(self, employee):
        """A manager may close above the difference limit, so a manager without
        a PIN would let anyone at the register do it: refuse until one is set."""
        self.ensure_one()
        if employee and self._vlux_api_is_manager(employee) and not employee.sudo().pin:
            raise ValidationError(_(
                "%s es encargado y no tiene PIN: ponle uno en Odoo (Empleados → Ajustes → PIN) antes de cerrar la caja.",
                employee.name,
            ))

    # --- prices ---------------------------------------------------------------

    def _vlux_api_pricelist(self, partner):
        """The register's pricelist, or the customer's when the register offers it."""
        self.ensure_one()
        pricelist = self.pricelist_id
        if partner and self.use_pricelist:
            own = partner.property_product_pricelist
            if own and own in self.available_pricelist_ids:
                pricelist = own
        return pricelist

    def _vlux_api_cash_rounding(self):
        """The rounding applied to the whole order, as ``pos.order._compute_prices`` does."""
        self.ensure_one()
        if self.cash_rounding and not self.only_round_cash_method and self.rounding_method:
            return self.rounding_method
        return None

    def _vlux_api_parse_lines(self, lines):
        if not isinstance(lines, list) or not lines:
            raise ValidationError(_("La venta no tiene productos."))
        if len(lines) > MAX_LINES:
            raise ValidationError(_("Una venta admite hasta %s líneas.", MAX_LINES))
        parsed = []
        for index, line in enumerate(lines, start=1):
            if not isinstance(line, dict):
                raise ValidationError(_("La línea %s no es un objeto.", index))
            try:
                product_id = int(line.get("product_id"))
                qty = float(line.get("qty", 1))
                price_unit = line.get("price_unit")
                price_unit = None if price_unit is None else float(price_unit)
            except (TypeError, ValueError):
                raise ValidationError(_("La línea %s tiene product_id, qty o price_unit inválidos.", index))
            if qty <= 0:
                raise ValidationError(_("La línea %s debe tener cantidad mayor que cero.", index))
            if price_unit is not None and price_unit < 0:
                raise ValidationError(_("La línea %s tiene precio negativo.", index))
            uuid = line.get("uuid")
            if uuid is not None and (not isinstance(uuid, str) or len(uuid) > 64):
                raise ValidationError(_("La línea %s tiene un uuid inválido.", index))
            parsed.append({"product_id": product_id, "qty": qty, "price_unit": price_unit, "uuid": uuid,
                           "price_from_barcode": bool(line.get("price_from_barcode"))})
        return parsed

    def _vlux_api_quote(self, lines, partner=None):
        """Price ``lines`` like ``pos.order`` would.

        ``lines``: ``[{"product_id", "qty", "price_unit"?, "uuid"?}]``. Returns
        the priced lines (with the records a ``pos.order.line`` needs) and the
        order totals from ``account.tax._get_tax_totals_summary``, the helper
        ``pos.order._compute_prices`` calls.
        """
        self.ensure_one()
        AccountTax = self.env["account.tax"]
        company = self.company_id
        currency = self.currency_id
        fiscal_position = self.default_fiscal_position_id
        pricelist = self._vlux_api_pricelist(partner)
        price_digits = self.env["decimal.precision"].precision_get("Product Price")
        parsed = self._vlux_api_parse_lines(lines)

        product_ids = {line["product_id"] for line in parsed}
        products = self.env["product.product"].search([
            ("id", "in", list(product_ids)),
            ("available_in_pos", "=", True),
            ("sale_ok", "=", True),
        ])
        by_id = {product.id: product for product in products}
        missing = sorted(product_ids - set(by_id))
        if missing:
            raise ValidationError(_(
                "Productos que no existen o no se venden en el punto de venta: %s",
                ", ".join(map(str, missing)),
            ))

        company_taxes = AccountTax._check_company_domain(company)
        partner_commercial = partner.commercial_partner_id if partner else self.env["res.partner"]
        priced, base_lines = [], []
        for index, line in enumerate(parsed):
            product = by_id[line["product_id"]]
            taxes = product.taxes_id.filtered_domain(company_taxes)
            line_taxes = fiscal_position.map_tax(taxes)
            if pricelist:
                catalog_price = pricelist._get_product_price(product, line["qty"], currency=currency)
            else:
                catalog_price = product.lst_price
            catalog_price = AccountTax._fix_tax_included_price_company(catalog_price, taxes, line_taxes, company)
            price_unit = catalog_price if line["price_unit"] is None else line["price_unit"]
            overridden = not line["price_from_barcode"] and (
                line["price_unit"] is not None
                and float_compare(price_unit, catalog_price, precision_digits=price_digits) != 0
            )
            base_lines.append(AccountTax._prepare_base_line_for_taxes_computation(
                None,
                id=index,
                partner_id=partner_commercial,
                currency_id=currency,
                product_id=product,
                product_uom_id=product.uom_id,
                tax_ids=line_taxes,
                price_unit=price_unit,
                quantity=line["qty"],
                discount=0.0,
            ))
            priced.append({
                "uuid": line["uuid"],
                "product": product,
                "qty": line["qty"],
                "price_unit": price_unit,
                "catalog_price": catalog_price,
                "price_overridden": overridden,
                # pos.order.line keeps the product taxes; the order's fiscal
                # position maps them when the line is priced again.
                "taxes": taxes,
            })

        AccountTax._add_tax_details_in_base_lines(base_lines, company)
        AccountTax._round_base_lines_tax_details(base_lines, company)
        totals = AccountTax._get_tax_totals_summary(
            base_lines=base_lines, currency=currency, company=company,
            cash_rounding=self._vlux_api_cash_rounding(),
        )
        for line, base_line in zip(priced, base_lines):
            details = base_line["tax_details"]
            excluded = details["total_excluded_currency"] + details.get("delta_total_excluded_currency", 0.0)
            tax_amount = sum(tax["tax_amount_currency"] for tax in details["taxes_data"])
            line["price_subtotal"] = currency.round(excluded)
            line["price_subtotal_incl"] = currency.round(excluded + tax_amount)
        return {
            "pricelist": pricelist,
            "fiscal_position": fiscal_position,
            "partner": partner or self.env["res.partner"],
            "lines": priced,
            "amount_untaxed": currency.round(totals["base_amount_currency"]),
            "amount_tax": currency.round(totals["tax_amount_currency"]),
            "amount_total": currency.round(totals["total_amount_currency"]),
        }

    def _vlux_api_amount_due(self, amount_total, has_cash):
        """What the customer must pay, after the register's cash rounding.

        Mirrors ``pos.order._get_rounded_amount``: with "only round cash"
        the rounding applies when cash is among the payments.
        """
        self.ensure_one()
        currency = self.currency_id
        method = self.rounding_method
        if self.cash_rounding and method and (not self.only_round_cash_method or has_cash):
            return currency.round(method.round(amount_total))
        return currency.round(amount_total)
