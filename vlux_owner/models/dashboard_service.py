from collections import defaultdict
from datetime import date as py_date, datetime, time, timedelta

import pytz

from odoo import api, fields, models


VALID_ORDER_STATES = ("paid", "done", "invoiced")


class VluxOwnerDashboardService(models.AbstractModel):
    _name = "vlux.owner.dashboard.service"
    _description = "VLUX Owner Dashboard Service"

    @api.model
    def _user_timezone(self):
        return pytz.timezone(self.env.user.tz or "UTC")

    @api.model
    def _local_date(self, value=None):
        if value:
            return fields.Date.to_date(value)
        return fields.Date.context_today(self)

    @api.model
    def _utc_bounds(self, local_day):
        tz = self._user_timezone()
        local_start = tz.localize(datetime.combine(local_day, time.min))
        local_end = local_start + timedelta(days=1)
        return (
            local_start.astimezone(pytz.UTC).replace(tzinfo=None),
            local_end.astimezone(pytz.UTC).replace(tzinfo=None),
        )

    @api.model
    def _orders_for_day(self, local_day):
        start_utc, end_utc = self._utc_bounds(local_day)
        return self.env["pos.order"].sudo().search([
            ("date_order", ">=", fields.Datetime.to_string(start_utc)),
            ("date_order", "<", fields.Datetime.to_string(end_utc)),
            ("state", "in", VALID_ORDER_STATES),
            ("company_id", "=", self.env.company.id),
        ], order="date_order asc, id asc")

    @api.model
    def _same_elapsed_yesterday_orders(self, local_day):
        tz = self._user_timezone()
        now_local = fields.Datetime.context_timestamp(self, fields.Datetime.now())
        if local_day != now_local.date():
            return self._orders_for_day(local_day - timedelta(days=1))

        yesterday = local_day - timedelta(days=1)
        local_start = tz.localize(datetime.combine(yesterday, time.min))
        local_end = local_start + timedelta(
            hours=now_local.hour,
            minutes=now_local.minute,
            seconds=now_local.second,
        )
        start_utc = local_start.astimezone(pytz.UTC).replace(tzinfo=None)
        end_utc = local_end.astimezone(pytz.UTC).replace(tzinfo=None)
        return self.env["pos.order"].sudo().search([
            ("date_order", ">=", fields.Datetime.to_string(start_utc)),
            ("date_order", "<", fields.Datetime.to_string(end_utc)),
            ("state", "in", VALID_ORDER_STATES),
            ("company_id", "=", self.env.company.id),
        ], order="date_order asc, id asc")

    @api.model
    def _money(self, value):
        return round(float(value or 0.0), 2)

    @api.model
    def get_dashboard(self, date=None):
        local_day = self._local_date(date)
        orders = self._orders_for_day(local_day)
        yesterday_orders = self._same_elapsed_yesterday_orders(local_day)

        order_ids = orders.ids
        lines = self.env["pos.order.line"].sudo().search([("order_id", "in", order_ids)]) if order_ids else self.env["pos.order.line"]

        sales_today = sum(orders.mapped("amount_total"))
        tickets = len(orders)
        units_sold = sum(lines.mapped("qty"))
        average_ticket = sales_today / tickets if tickets else 0.0
        sales_yesterday = sum(yesterday_orders.mapped("amount_total"))
        comparison = None
        if sales_yesterday:
            comparison = ((sales_today - sales_yesterday) / sales_yesterday) * 100.0

        tz = self._user_timezone()
        hourly = defaultdict(float)
        for order in orders:
            local_dt = pytz.UTC.localize(order.date_order).astimezone(tz)
            hourly[local_dt.hour] += order.amount_total
        sales_trend = [
            {"label": f"{hour:02d}", "amount": self._money(hourly.get(hour, 0.0))}
            for hour in range(0, 24, 2)
        ]

        register_totals = defaultdict(float)
        register_state = {}
        register_names = {}
        for order in orders:
            config = order.session_id.config_id
            if not config:
                continue
            register_totals[config.id] += order.amount_total
            register_names[config.id] = config.name
            register_state[config.id] = "open" if order.session_id.state in ("opening_control", "opened", "closing_control") else "closed"

        sales_by_register = []
        for config_id, amount in sorted(register_totals.items(), key=lambda item: item[1], reverse=True):
            share = (amount / sales_today * 100.0) if sales_today else 0.0
            sales_by_register.append({
                "id": config_id,
                "name": register_names.get(config_id, f"Caja {config_id}"),
                "amount": self._money(amount),
                "share_pct": round(share, 1),
                "state": register_state.get(config_id, "unknown"),
            })

        product_totals = defaultdict(lambda: {"qty": 0.0, "amount": 0.0, "name": ""})
        for line in lines:
            product = line.product_id
            if not product:
                continue
            row = product_totals[product.id]
            row["name"] = product.display_name
            row["qty"] += line.qty
            row["amount"] += line.price_subtotal_incl
        top_products = [
            {
                "id": product_id,
                "name": row["name"],
                "qty": round(row["qty"], 2),
                "amount": self._money(row["amount"]),
            }
            for product_id, row in sorted(product_totals.items(), key=lambda item: item[1]["qty"], reverse=True)[:10]
        ]

        latest_orders = orders.sorted(key=lambda order: (order.date_order, order.id), reverse=True)[:10]
        latest_sales = []
        for order in latest_orders:
            local_dt = pytz.UTC.localize(order.date_order).astimezone(tz)
            latest_sales.append({
                "id": order.id,
                "reference": order.pos_reference or order.name,
                "time": local_dt.strftime("%H:%M"),
                "amount": self._money(order.amount_total),
                "register": order.session_id.config_id.name or "Caja",
                "cashier": order.user_id.name or "Usuario",
            })

        threshold = float(self.env["ir.config_parameter"].sudo().get_param("vlux_owner.low_stock_threshold", "10") or 10)
        Product = self.env["product.product"].sudo().with_company(self.env.company)
        product_domain = [
            ("active", "=", True),
            ("available_in_pos", "=", True),
        ]
        # Odoo 19 distingue bienes/servicios y solo los bienes rastreados tienen
        # inventario real. Detectamos el campo disponible para mantener compatibilidad.
        if "is_storable" in Product._fields:
            product_domain.append(("is_storable", "=", True))
        elif "type" in Product._fields:
            product_domain.append(("type", "!=", "service"))
        products = Product.search(product_domain, limit=500)
        low_stock_rows = []
        for product in products:
            qty = product.qty_available
            if qty <= threshold:
                low_stock_rows.append({
                    "id": product.id,
                    "name": product.display_name,
                    "qty_available": round(qty, 2),
                    "threshold": threshold,
                })
        low_stock_rows.sort(key=lambda row: row["qty_available"])

        currency = self.env.company.currency_id
        now_local = fields.Datetime.context_timestamp(self, fields.Datetime.now())
        return {
            "generated_at": fields.Datetime.to_string(fields.Datetime.now()) + "Z",
            "date_label": local_day.strftime("%d/%m/%Y"),
            "currency": {
                "symbol": currency.symbol or "$",
                "position": currency.position or "before",
                "decimals": currency.decimal_places,
            },
            "store": {
                "company_name": self.env.company.name,
                "user_name": self.env.user.name.split(" ")[0] if self.env.user.name else "Propietario",
            },
            "summary": {
                "sales_today": self._money(sales_today),
                "tickets": tickets,
                "units_sold": round(units_sold, 2),
                "average_ticket": self._money(average_ticket),
                "comparison_vs_yesterday_pct": round(comparison, 1) if comparison is not None else None,
            },
            "sales_trend": sales_trend,
            "sales_by_register": sales_by_register,
            "top_products": top_products,
            "latest_sales": latest_sales,
            "low_stock": low_stock_rows[:20],
            "meta": {
                "timezone": str(tz),
                "comparison_mode": "same_elapsed_time" if local_day == now_local.date() else "full_day",
                "order_states": list(VALID_ORDER_STATES),
            },
        }
