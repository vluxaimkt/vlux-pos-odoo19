from datetime import datetime

from freezegun import freeze_time

from odoo.fields import Command
from odoo.tests.common import TransactionCase, tagged

# 2020-01-16 05:55 UTC = 2020-01-15 23:55 en America/Mexico_City (UTC-6, sin horario de verano).
NOW_UTC = "2020-01-16 05:55:00"
USER_TZ = "America/Mexico_City"


@tagged("post_install", "-at_install")
class TestVluxOwnerDashboard(TransactionCase):
    """Contrato numérico del dashboard con datos sintéticos conocidos.

    Todo vive en una compañía propia para no depender del contenido de la DB.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        ICP = env["ir.config_parameter"].sudo()
        ICP.set_param("vlux_owner.dashboard_cache_ttl", "0")
        ICP.set_param("vlux_owner.low_stock_threshold", "10")

        cls.company = env["res.company"].sudo().create({"name": "VLUX Owner Dashboard Co"})
        cls.other_company = env["res.company"].sudo().create({"name": "VLUX Owner Dashboard Other"})
        cls.owner = (
            env["res.users"]
            .with_context(no_reset_password=True)
            .sudo()
            .create({
                "name": "Dueña Dashboard",
                "login": "vlux-owner-dashboard",
                "email": "vlux-owner-dashboard@example.test",
                "tz": USER_TZ,
                "company_id": cls.company.id,
                "company_ids": [Command.set(cls.company.ids)],
                "group_ids": [Command.set([
                    env.ref("base.group_user").id,
                    env.ref("vlux_core.group_vlux_owner").id,
                ])],
            })
        )

        payment = env["pos.payment.method"].with_company(cls.company).sudo().create(
            {"name": "VLUX Owner Pay Later", "company_id": cls.company.id}
        )
        Config = env["pos.config"].with_company(cls.company).sudo()
        cls.config_open = Config.create({
            "name": "Caja Abierta",
            "company_id": cls.company.id,
            "payment_method_ids": [Command.link(payment.id)],
        })
        cls.config_closed = Config.create({
            "name": "Caja Cerrada",
            "company_id": cls.company.id,
            "payment_method_ids": [Command.link(payment.id)],
        })
        Session = env["pos.session"].with_company(cls.company).sudo()
        cls.session_open = Session.create({"config_id": cls.config_open.id, "user_id": env.uid})
        cls.session_closed = Session.create({"config_id": cls.config_closed.id, "user_id": env.uid})
        cls.session_closed.write({"state": "closed", "stop_at": datetime(2020, 1, 15, 20, 0)})

        # Nada más debe aparecer en stock bajo que los productos de este test.
        env["product.template"].sudo().search([("available_in_pos", "=", True)]).write({"available_in_pos": False})

        Product = env["product.product"].sudo()
        sold = {"type": "consu", "is_storable": False, "available_in_pos": True}
        cls.p1 = Product.create({**sold, "name": "VLUXDASH Uno", "list_price": 50})
        cls.p2 = Product.create({**sold, "name": "VLUXDASH Dos", "list_price": 60})
        cls.p3 = Product.create({**sold, "name": "VLUXDASH Tres", "list_price": 10})

        storable = {"type": "consu", "is_storable": True, "available_in_pos": True}
        cls.stock_zero = Product.create({**storable, "name": "VLUXDASH Sin existencias"})
        cls.stock_low = Product.create({**storable, "name": "VLUXDASH Bajo"})
        cls.stock_negative = Product.create({**storable, "name": "VLUXDASH Negativo"})
        cls.stock_high = Product.create({**storable, "name": "VLUXDASH Alto"})
        cls.stock_threshold = Product.create({**storable, "name": "VLUXDASH En umbral"})
        cls.stock_archived = Product.create({**storable, "name": "VLUXDASH Archivado", "active": False})
        cls.stock_not_pos = Product.create({**storable, "name": "VLUXDASH Fuera POS", "available_in_pos": False})
        cls.stock_foreign = Product.create({**storable, "name": "VLUXDASH Otra empresa", "company_id": cls.other_company.id})

        stock = env["stock.warehouse"].sudo().search([("company_id", "=", cls.company.id)], limit=1).lot_stock_id
        other_stock = env["stock.warehouse"].sudo().search([("company_id", "=", cls.other_company.id)], limit=1).lot_stock_id
        customers = env.ref("stock.stock_location_customers")
        Quant = env["stock.quant"].sudo()
        Quant.create([
            {"product_id": cls.stock_low.id, "location_id": stock.id, "quantity": 3},
            {"product_id": cls.stock_negative.id, "location_id": stock.id, "quantity": -2},
            {"product_id": cls.stock_high.id, "location_id": stock.id, "quantity": 50},
            {"product_id": cls.stock_threshold.id, "location_id": stock.id, "quantity": 4},
            {"product_id": cls.stock_threshold.id, "location_id": stock.id, "quantity": 6},
            # Existencias de otra compañía o fuera de ubicaciones internas no cuentan.
            {"product_id": cls.stock_zero.id, "location_id": other_stock.id, "quantity": 100},
            {"product_id": cls.stock_low.id, "location_id": customers.id, "quantity": 500},
        ])

        # Horas en UTC; comentario = hora local (UTC-6).
        # Ayer (15/ene local = 14/ene), dentro del mismo tiempo transcurrido (< 23:55):
        cls._order(cls.session_open, "2020-01-14 07:00:00", [(cls.p2, 1, 100)], "Y-01")   # 14/ene 01:00
        cls._order(cls.session_open, "2020-01-15 05:30:00", [(cls.p1, 2, 50)], "Y-02")    # 14/ene 23:30
        # Ayer, pero después de las 23:55: solo cuenta para el día completo.
        cls._order(cls.session_open, "2020-01-15 05:58:00", [(cls.p1, 1, 40)], "Y-03")    # 14/ene 23:58
        # Hoy (15/ene local):
        cls.order_b = cls._order(cls.session_open, "2020-01-15 06:30:00", [(cls.p1, 2, 50)], "T-01")  # 00:30
        cls.order_c = cls._order(
            cls.session_closed, "2020-01-15 15:10:00", [(cls.p2, 1, 60), (cls.p3, 3, 10)], "T-02"
        )  # 09:10 (hora impar -> bloque "08")
        cls.order_d = cls._order(
            cls.session_open, "2020-01-16 05:50:00", [(cls.p1, 1, 50), (cls.p3, 1, 10)], "T-03"
        )  # 23:50
        # Excluidas: borrador de hoy y orden de hoy de otra compañía.
        cls._order(cls.session_open, "2020-01-15 18:00:00", [(cls.p2, 10, 60)], "X-DRAFT", state="draft")
        cls._order(
            cls.session_open, "2020-01-15 18:00:00", [(cls.p2, 7, 60)], "X-OTHER", company=cls.other_company
        )

    @classmethod
    def _order(cls, session, date_order, lines, reference, state="paid", company=None):
        total = sum(qty * price for _product, qty, price in lines)
        return cls.env["pos.order"].sudo().with_context(tracking_disable=True).create({
            "session_id": session.id,
            "company_id": (company or cls.company).id,
            "user_id": cls.env.uid,
            "pos_reference": f"VLUXDASH/{reference}",
            "date_order": date_order,
            "state": state,
            "amount_tax": 0.0,
            "amount_total": total,
            "amount_paid": total if state != "draft" else 0.0,
            "amount_return": 0.0,
            "lines": [
                Command.create({
                    "name": "VLUXDASH line",
                    "product_id": product.id,
                    "qty": qty,
                    "price_unit": price,
                    "price_subtotal": qty * price,
                    "price_subtotal_incl": qty * price,
                })
                for product, qty, price in lines
            ],
        })

    def _service(self):
        return self.env["vlux.owner.dashboard.service"].with_user(self.owner).with_company(self.company)

    @freeze_time(NOW_UTC)
    def test_summary_uses_local_day_and_same_elapsed_comparison(self):
        data = self._service().get_dashboard()

        self.assertEqual(data["date_label"], "15/01/2020")
        self.assertEqual(data["meta"]["timezone"], USER_TZ)
        self.assertEqual(data["meta"]["comparison_mode"], "same_elapsed_time")
        self.assertEqual(data["summary"], {
            "sales_today": 250.0,
            "tickets": 3,
            "units_sold": 8.0,
            "average_ticket": 83.33,
            # Ayer hasta las 23:55: 100 + 100 = 200 -> +25 %.
            "comparison_vs_yesterday_pct": 25.0,
        })

    @freeze_time(NOW_UTC)
    def test_past_day_compares_full_previous_day(self):
        data = self._service().get_dashboard(date="2020-01-14")

        self.assertEqual(data["meta"]["comparison_mode"], "full_day")
        self.assertEqual(data["summary"]["sales_today"], 240.0)
        self.assertEqual(data["summary"]["tickets"], 3)
        self.assertIsNone(data["summary"]["comparison_vs_yesterday_pct"])

        data = self._service().get_dashboard(date="2020-01-16")
        self.assertEqual(data["summary"]["tickets"], 0)
        # El 16/ene se compara contra el 15/ene completo.
        self.assertEqual(data["summary"]["comparison_vs_yesterday_pct"], -100.0)

    @freeze_time(NOW_UTC)
    def test_sales_trend_buckets_include_odd_hours(self):
        trend = self._service().get_dashboard()["sales_trend"]

        self.assertEqual([point["label"] for point in trend], [f"{h:02d}" for h in range(0, 24, 2)])
        amounts = {point["label"]: point["amount"] for point in trend}
        self.assertEqual(amounts["00"], 100.0)
        self.assertEqual(amounts["08"], 90.0)  # venta de las 09:10
        self.assertEqual(amounts["22"], 60.0)  # venta de las 23:50
        self.assertEqual(sum(amounts.values()), 250.0)

    @freeze_time(NOW_UTC)
    def test_sales_by_register_with_current_state(self):
        registers = self._service().get_dashboard()["sales_by_register"]

        self.assertEqual(registers, [
            {"id": self.config_open.id, "name": "Caja Abierta", "amount": 160.0, "share_pct": 64.0, "state": "open"},
            {"id": self.config_closed.id, "name": "Caja Cerrada", "amount": 90.0, "share_pct": 36.0, "state": "closed"},
        ])

    @freeze_time(NOW_UTC)
    def test_top_products_ordered_by_quantity(self):
        top = self._service().get_dashboard()["top_products"]

        self.assertEqual(
            [(row["id"], row["qty"], row["amount"]) for row in top],
            [(self.p3.id, 4.0, 40.0), (self.p1.id, 3.0, 150.0), (self.p2.id, 1.0, 60.0)],
        )
        self.assertEqual(top[0]["name"], self.p3.display_name)

    @freeze_time(NOW_UTC)
    def test_latest_sales_in_local_time(self):
        latest = self._service().get_dashboard()["latest_sales"]

        self.assertEqual(
            [(row["id"], row["reference"], row["time"], row["amount"], row["register"]) for row in latest],
            [
                (self.order_d.id, "VLUXDASH/T-03", "23:50", 60.0, "Caja Abierta"),
                (self.order_c.id, "VLUXDASH/T-02", "09:10", 90.0, "Caja Cerrada"),
                (self.order_b.id, "VLUXDASH/T-01", "00:30", 100.0, "Caja Abierta"),
            ],
        )

    @freeze_time(NOW_UTC)
    def test_low_stock_matches_internal_company_quantities(self):
        low = self._service().get_dashboard()["low_stock"]

        self.assertEqual(
            [(row["id"], row["qty_available"]) for row in low],
            [
                (self.stock_negative.id, -2.0),
                (self.stock_zero.id, 0.0),
                (self.stock_low.id, 3.0),
                (self.stock_threshold.id, 10.0),
            ],
        )
        self.assertTrue(all(row["threshold"] == 10.0 for row in low))
        # Mismo resultado que el cálculo estándar de Odoo.
        Product = self.env["product.product"].sudo().with_company(self.company)
        for row in low:
            self.assertEqual(Product.browse(row["id"]).qty_available, row["qty_available"])

    @freeze_time(NOW_UTC)
    def test_other_company_sees_nothing_from_this_company(self):
        service = self.env["vlux.owner.dashboard.service"].with_company(self.other_company).with_context(tz=USER_TZ)
        data = service.get_dashboard()

        self.assertEqual(data["summary"]["tickets"], 1)  # solo X-OTHER
        self.assertEqual(data["summary"]["sales_today"], 420.0)
        self.assertEqual(data["store"]["company_name"], self.other_company.name)
        self.assertEqual(
            [row["id"] for row in data["sales_by_register"]], [self.config_open.id]
        )
        low_ids = {row["id"] for row in data["low_stock"]}
        # En la otra compañía: su producto propio sin existencias sí aparece; el
        # compartido con 100 unidades allí no.
        self.assertIn(self.stock_foreign.id, low_ids)
        self.assertNotIn(self.stock_zero.id, low_ids)

    @freeze_time(NOW_UTC)
    def test_query_count_does_not_grow_with_orders(self):
        service = self._service()
        service.get_dashboard()  # calienta ormcache (parámetros, grupos)

        def count_queries():
            self.env.invalidate_all()
            before = self.env.cr.sql_log_count
            service.get_dashboard()
            return self.env.cr.sql_log_count - before

        small = count_queries()
        for index in range(60):
            self._order(
                self.session_open,
                "2020-01-15 16:%02d:00" % (index % 60),
                [(self.p1, 1, 50), (self.p2, 1, 60), (self.p3, 2, 10)],
                f"BULK-{index}",
            )
        large = count_queries()

        self.assertLessEqual(large, small)
        self.assertLessEqual(large, 25)
        self.assertEqual(service.get_dashboard()["summary"]["tickets"], 63)

    @freeze_time(NOW_UTC)
    def test_short_cache_serves_same_metrics_until_cleared(self):
        self.env["ir.config_parameter"].sudo().set_param("vlux_owner.dashboard_cache_ttl", "60")
        service = self._service()
        service._clear_dashboard_cache()
        self.addCleanup(service._clear_dashboard_cache)

        self.assertEqual(service.get_dashboard()["summary"]["tickets"], 3)
        self._order(self.session_open, "2020-01-15 16:00:00", [(self.p1, 1, 50)], "CACHE-1")
        cached = service.get_dashboard()
        self.assertEqual(cached["summary"]["tickets"], 3)
        # La parte por usuario no sale de la caché.
        self.assertEqual(cached["store"]["user_name"], "Dueña")

        service._clear_dashboard_cache()
        self.assertEqual(service.get_dashboard()["summary"]["tickets"], 4)
