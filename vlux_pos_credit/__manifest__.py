{
    "name": "VLUX POS Credit",
    "summary": "Ventas a crédito (fiado) en el POS: clientes autorizados, límite y saldo",
    "description": (
        "Venta a credito con la forma de pago Credito (cuenta de cliente de Odoo). "
        "Cada cliente tiene credito autorizado, limite y saldo. Solo el encargado y "
        "el dueno venden a credito; una venta que rompe la regla se marca para el "
        "dueno, nunca se pierde."
    ),
    "version": "19.0.1.7.0",
    "category": "Sales/Point of Sale",
    "author": "VLUX",
    "website": "https://vlux.com.mx",
    "license": "LGPL-3",
    "depends": ["point_of_sale", "pos_hr", "vlux_core", "vlux_pos_api"],
    "data": [
        "security/ir.model.access.csv",
        "views/credit_opening_views.xml",
        "views/res_config_settings_views.xml",
        "views/res_partner_views.xml",
        "views/pos_order_views.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "vlux_pos_credit/static/src/pos/**/*.js",
            "vlux_pos_credit/static/src/pos/**/*.xml",
        ],
        "web.assets_tests": [
            "vlux_pos_credit/static/tests/tours/**/*",
        ],
    },
    "installable": True,
    "application": False,
}
