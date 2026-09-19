import copy
import threading
import time as time_module
from datetime import datetime, time, timedelta

import pytz

from odoo import api, fields, models


VALID_ORDER_STATES = ("paid", "done", "invoiced")
OPEN_SESSION_STATES = ("opening_control", "opened", "closing_control")
TREND_BUCKET_HOURS = 2
TOP_PRODUCTS_LIMIT = 10
LATEST_SALES_LIMIT = 10
LOW_STOCK_LIMIT = 20

# Caché corta en memoria por proceso (cada worker de Odoo tiene la suya). El
# dashboard se refresca cada 30 s por cliente; con varios dueños/pestañas
# abiertas sobre la misma compañía, la caché evita recalcular las mismas
# agregaciones. TTL configurable con ``vlux_owner.dashboard_cache_ttl``
# (segundos, 0 la desactiva).
DEFAULT_CACHE_TTL = 15
MAX_CACHE_TTL = 300
_CACHE_MAX_ENTRIES = 256
_cache = {}
_cache_lock = threading.Lock()


class VluxOwnerDashboardService(models.AbstractModel):
    _name = "vlux.owner.dashboard.service"
    _description = "VLUX Owner Dashboard Service"

    # ------------------------------------------------------------------
    # Tiempo y dominios
    # ------------------------------------------------------------------

    @api.model
    def _user_timezone(self):
        name = self.env.context.get("tz") or self.env.user.tz or "UTC"
        if name not in pytz.all_timezones_set:
            name = "UTC"
        return pytz.timezone(name)

    @api.model
    def _local_date(self, value=None):
        if value:
            return fields.Date.to_date(value)
        return fields.Date.context_today(self)

    @api.model
    def _local_now(self):
        return pytz.UTC.localize(fields.Datetime.now()).astimezone(self._user_timezone())

    @api.model
    def _to_utc(self, local_dt):
        return local_dt.astimezone(pytz.UTC).replace(tzinfo=None)

    @api.model
    def _utc_bounds(self, local_day):
        tz = self._user_timezone()
        local_start = tz.localize(datetime.combine(local_day, time.min))
        local_end = tz.localize(datetime.combine(local_day + timedelta(days=1), time.min))
        return self._to_utc(local_start), self._to_utc(local_end)

    @api.model
    def _order_domain(self, start_utc, end_utc):
        return [
            ("date_order", ">=", fields.Datetime.to_string(start_utc)),
            ("date_order", "<", fields.Datetime.to_string(end_utc)),
            ("state", "in", VALID_ORDER_STATES),
            ("company_id", "=", self.env.company.id),
        ]

    @api.model
    def _day_domain(self, local_day):
        return self._order_domain(*self._utc_bounds(local_day))

    @api.model
    def _comparison_domain(self, local_day, now_local):
        """Ayer a la misma hora si ``local_day`` es hoy; si no, el día anterior completo."""
        yesterday = local_day - timedelta(days=1)
        if local_day != now_local.date():
            return self._day_domain(yesterday)
        tz = self._user_timezone()
        start_utc, _end = self._utc_bounds(yesterday)
        elapsed_local = tz.localize(datetime.combine(yesterday, now_local.time().replace(microsecond=0, tzinfo=None)))
        return self._order_domain(start_utc, self._to_utc(elapsed_local))

    @api.model
    def _orders_for_day(self, local_day):
        return self.env["pos.order"].sudo().search(self._day_domain(local_day), order="date_order asc, id asc")

    @api.model
    def _money(self, value):
        return round(float(value or 0.0), 2)

    # ------------------------------------------------------------------
    # Métricas (todas agregadas en PostgreSQL)
    # ------------------------------------------------------------------

    @api.model
    def _hourly_totals(self, Order, day_domain):
        """{hora_local: (monto, tickets)}; la hora se calcula en SQL con la tz del usuario."""
        rows = Order.with_context(tz=self._user_timezone().zone)._read_group(
            day_domain, ["date_order:hour_number"], ["amount_total:sum", "__count"],
        )
        return {int(hour): (amount or 0.0, count) for hour, amount, count in rows if hour is not None}

    @api.model
    def _sales_trend(self, hourly):
        buckets = {}
        for hour, (amount, _count) in hourly.items():
            bucket = hour - hour % TREND_BUCKET_HOURS
            buckets[bucket] = buckets.get(bucket, 0.0) + amount
        return [
            {"label": f"{bucket:02d}", "amount": self._money(buckets.get(bucket, 0.0))}
            for bucket in range(0, 24, TREND_BUCKET_HOURS)
        ]

    @api.model
    def _sales_by_register(self, Order, day_domain, sales_today):
        rows = Order._read_group(
            day_domain + [("config_id", "!=", False)],
            ["config_id"],
            ["amount_total:sum"],
            order="amount_total:sum desc",
        )
        configs = self.env["pos.config"].sudo().browse([config.id for config, _amount in rows])
        open_configs = {
            config.id
            for [config] in self.env["pos.session"].sudo()._read_group(
                [("config_id", "in", configs.ids), ("state", "in", OPEN_SESSION_STATES)],
                ["config_id"],
            )
        }
        result = []
        for config, amount in rows:
            share = (amount / sales_today * 100.0) if sales_today else 0.0
            result.append({
                "id": config.id,
                "name": config.name or f"Caja {config.id}",
                "amount": self._money(amount),
                "share_pct": round(share, 1),
                "state": "open" if config.id in open_configs else "closed",
            })
        return result

    @api.model
    def _units_and_top_products(self, day_domain):
        Line = self.env["pos.order.line"].sudo()
        line_domain = [("order_id", "any", day_domain)]
        [[units]] = Line._read_group(line_domain, [], ["qty:sum"])
        rows = Line._read_group(
            line_domain + [("product_id", "!=", False)],
            ["product_id"],
            ["qty:sum", "price_subtotal_incl:sum"],
            order="qty:sum desc, price_subtotal_incl:sum desc, product_id asc",
            limit=TOP_PRODUCTS_LIMIT,
        )
        top_products = [
            {
                "id": product.id,
                "name": product.display_name,
                "qty": round(qty or 0.0, 2),
                "amount": self._money(amount),
            }
            for product, qty, amount in rows
        ]
        return units or 0.0, top_products

    @api.model
    def _latest_sales(self, Order, day_domain):
        tz = self._user_timezone()
        orders = Order.search_fetch(
            day_domain,
            ["name", "pos_reference", "date_order", "amount_total", "config_id", "user_id"],
            order="date_order desc, id desc",
            limit=LATEST_SALES_LIMIT,
        )
        return [
            {
                "id": order.id,
                "reference": order.pos_reference or order.name,
                "time": pytz.UTC.localize(order.date_order).astimezone(tz).strftime("%H:%M"),
                "amount": self._money(order.amount_total),
                "register": order.config_id.name or "Caja",
                "cashier": order.user_id.name or "Usuario",
            }
            for order in orders
        ]

    @api.model
    def _low_stock_threshold(self):
        value = self.env["ir.config_parameter"].sudo().get_param("vlux_owner.low_stock_threshold", "10")
        try:
            return float(value or 10)
        except (TypeError, ValueError):
            return 10.0

    @api.model
    def _low_stock(self, threshold):
        """Productos POS almacenables con existencias internas <= umbral.

        Equivale a ``qty_available`` en ubicaciones internas de la compañía, pero
        sin recorrer productos en Python: una agregación sobre ``stock.quant``
        para los que tienen existencias y una búsqueda acotada para los que no
        tienen ningún quant (existencia 0).
        """
        company = self.env.company
        Product = self.env["product.product"].sudo().with_company(company)
        product_domain = [
            ("active", "=", True),
            ("available_in_pos", "=", True),
            ("company_id", "in", [False, company.id]),
        ]
        # Odoo 19 distingue bienes/servicios y solo los bienes rastreados tienen
        # inventario real. Detectamos el campo disponible para mantener compatibilidad.
        if "is_storable" in Product._fields:
            product_domain.append(("is_storable", "=", True))
        elif "type" in Product._fields:
            product_domain.append(("type", "!=", "service"))
        quant_domain = [
            ("company_id", "=", company.id),
            ("location_id.usage", "=", "internal"),
        ]

        candidates = []
        rows = self.env["stock.quant"].sudo()._read_group(
            quant_domain + [("product_id", "any", product_domain)],
            ["product_id"],
            ["quantity:sum"],
            having=[("quantity:sum", "<=", threshold)],
            order="quantity:sum asc, product_id asc",
            limit=LOW_STOCK_LIMIT,
        )
        candidates.extend((qty or 0.0, product) for product, qty in rows)
        if threshold >= 0:
            without_stock = Product.search(
                product_domain + [("stock_quant_ids", "not any", quant_domain)],
                order="id asc",
                limit=LOW_STOCK_LIMIT,
            )
            candidates.extend((0.0, product) for product in without_stock)

        candidates.sort(key=lambda item: (item[0], item[1].id))
        return [
            {
                "id": product.id,
                "name": product.display_name,
                "qty_available": round(qty, 2),
                "threshold": threshold,
            }
            for qty, product in candidates[:LOW_STOCK_LIMIT]
        ]

    @api.model
    def _compute_metrics(self, local_day, now_local):
        Order = self.env["pos.order"].sudo()
        day_domain = self._day_domain(local_day)

        hourly = self._hourly_totals(Order, day_domain)
        sales_today = sum(amount for amount, _count in hourly.values())
        tickets = sum(count for _amount, count in hourly.values())
        [[sales_yesterday]] = Order._read_group(
            self._comparison_domain(local_day, now_local), [], ["amount_total:sum"],
        )
        sales_yesterday = sales_yesterday or 0.0
        comparison = None
        if sales_yesterday:
            comparison = ((sales_today - sales_yesterday) / sales_yesterday) * 100.0
        units_sold, top_products = self._units_and_top_products(day_domain)
        threshold = self._low_stock_threshold()

        return {
            "generated_at": fields.Datetime.to_string(fields.Datetime.now()) + "Z",
            "summary": {
                "sales_today": self._money(sales_today),
                "tickets": tickets,
                "units_sold": round(units_sold, 2),
                "average_ticket": self._money(sales_today / tickets if tickets else 0.0),
                "comparison_vs_yesterday_pct": round(comparison, 1) if comparison is not None else None,
            },
            "sales_trend": self._sales_trend(hourly),
            "sales_by_register": self._sales_by_register(Order, day_domain, sales_today),
            "top_products": top_products,
            "latest_sales": self._latest_sales(Order, day_domain),
            "low_stock": self._low_stock(threshold),
        }

    # ------------------------------------------------------------------
    # Caché
    # ------------------------------------------------------------------

    @api.model
    def _cache_ttl(self):
        value = self.env["ir.config_parameter"].sudo().get_param(
            "vlux_owner.dashboard_cache_ttl", str(DEFAULT_CACHE_TTL)
        )
        try:
            return max(0, min(int(value), MAX_CACHE_TTL))
        except (TypeError, ValueError):
            return DEFAULT_CACHE_TTL

    @api.model
    def _cached_metrics(self, local_day, now_local):
        ttl = self._cache_ttl()
        if not ttl:
            return self._compute_metrics(local_day, now_local)
        key = (
            self.env.cr.dbname,
            self.env.company.id,
            self._user_timezone().zone,
            local_day.isoformat(),
            local_day == now_local.date(),
        )
        now = time_module.monotonic()
        with _cache_lock:
            entry = _cache.get(key)
            if entry and entry[0] > now:
                return copy.deepcopy(entry[1])
        metrics = self._compute_metrics(local_day, now_local)
        with _cache_lock:
            if len(_cache) >= _CACHE_MAX_ENTRIES:
                for stale_key in [k for k, (expires, _v) in _cache.items() if expires <= now]:
                    del _cache[stale_key]
                if len(_cache) >= _CACHE_MAX_ENTRIES:
                    _cache.clear()
            _cache[key] = (now + ttl, copy.deepcopy(metrics))
        return metrics

    @api.model
    def _clear_dashboard_cache(self):
        with _cache_lock:
            _cache.clear()

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    @api.model
    def get_dashboard(self, date=None):
        local_day = self._local_date(date)
        now_local = self._local_now()
        metrics = self._cached_metrics(local_day, now_local)

        currency = self.env.company.currency_id
        return {
            "generated_at": metrics["generated_at"],
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
            "summary": metrics["summary"],
            "sales_trend": metrics["sales_trend"],
            "sales_by_register": metrics["sales_by_register"],
            "top_products": metrics["top_products"],
            "latest_sales": metrics["latest_sales"],
            "low_stock": metrics["low_stock"],
            "meta": {
                "timezone": self._user_timezone().zone,
                "comparison_mode": "same_elapsed_time" if local_day == now_local.date() else "full_day",
                "order_states": list(VALID_ORDER_STATES),
            },
        }
