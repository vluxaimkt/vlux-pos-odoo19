{
    "name": "VLUX Mobile Scanner",
    "summary": "Convierte un telefono en lector inalambrico para Odoo POS",
    "description": (
        "Empareja un telefono con una caja POS mediante codigo temporal y "
        "envia codigos de barras al flujo estandar de escaneo de Odoo POS."
    ),
    "version": "19.0.2.0.1",
    "category": "Sales/Point of Sale",
    "author": "VLUX",
    "website": "https://vlux.com.mx",
    "license": "LGPL-3",
    "depends": ["vlux_core", "point_of_sale", "bus", "web"],
    "external_dependencies": {"python": ["qrcode"]},
    "data": [
        "security/ir.model.access.csv",
        "security/vlux_mobile_scanner_security.xml",
        "data/ir_cron.xml",
        "views/scanner_templates.xml",
        "views/scanner_log_views.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "vlux_mobile_scanner/static/src/pos/**/*.js",
            "vlux_mobile_scanner/static/src/pos/**/*.xml",
            "vlux_mobile_scanner/static/src/pos/**/*.scss",
        ],
        "web.assets_tests": [
            "vlux_mobile_scanner/static/tests/tours/**/*",
        ],
    },
    "installable": True,
    "application": False,
}
