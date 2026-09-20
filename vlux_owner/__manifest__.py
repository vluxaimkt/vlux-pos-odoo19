{
    "name": "VLUX Owner",
    "summary": "Dashboard móvil PWA para propietarios conectado a Odoo POS",
    "version": "19.0.1.5.0",
    "category": "Point of Sale",
    "author": "VLUX",
    "license": "LGPL-3",
    "depends": ["vlux_core", "point_of_sale", "stock", "web"],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "views/templates.xml",
    ],
    "installable": True,
    "application": True,
}
