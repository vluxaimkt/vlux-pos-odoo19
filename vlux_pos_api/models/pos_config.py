"""What a VLUX register asks of its ``pos.config``: who may work it and what a cart costs.

Prices and taxes are computed here, on the server, with the same engine
``pos.order._compute_prices`` uses, so the total a customer is charged is the
total Odoo books. A client may send the unit price it charged (an offline
register prices from its local copy of the catalog); a price that differs
from the catalog is accepted and reported, never silently replaced.
"""
from odoo import _, models
from odoo.exceptions import ValidationError
from odoo.fields import Domain
from odoo.tools import float_compare

# Lines per order: a guard against a runaway client, far above any real ticket.
MAX_LINES = 500


class PosConfig(models.Model):
    _inherit = "pos.config"

    # --- employees (pos_hr) -------------------------------------------------

    def _vlux_api_employee_records(self):
        self.ensure_one()
        # sudo: a cashier cannot read hr.employee, yet the Odoo POS loads the
        # same list for them. The domain keeps it to this register's company
        # and allowed employees.
        return self.env["hr.employee"].sudo().search(self._employee_domain(self.env.uid), order="name, id")

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
            }
            for row in rows
        ]

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
            parsed.append({"product_id": product_id, "qty": qty, "price_unit": price_unit, "uuid": uuid})
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
            overridden = (
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
