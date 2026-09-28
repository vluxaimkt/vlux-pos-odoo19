{
    "name": "VLUX POS Credit",
    "summary": "Ventas a crédito (fiado) en el POS: clientes autorizados, límite y saldo",
    "description": (
        "Venta a credito con la forma de pago Credito (cuenta de cliente de Odoo). "
        "Cada cliente tiene credito autorizado, limite y saldo. Solo el encargado y "
        "el dueno venden a credito; una venta que rompe la regla se marca para el "
        "dueno, nunca se pierde."
    ),
    "version": "19.0.1.0.0",
    "category": "Sales/Point of Sale",
    "author": "VLUX",
    "website": "https://vlux.com.mx",
    "license": "LGPL-3",
    "depends": ["point_of_sale", "pos_hr", "vlux_core"],
    "data": [
        "views/res_partner_views.xml",
        "views/pos_order_views.xml",
    ],
    "installable": True,
    "application": False,
}
