{
    "name": "VLUX POS Catalog",
    "summary": "Alta rapida de productos desde el POS al escanear un codigo desconocido",
    "description": (
        "Cuando el POS recibe un codigo de barras sin producto, ofrece registrar el "
        "producto con un formulario minimo (codigo, nombre, precio, foto manual) y lo "
        "agrega a la orden sin recargar el POS. La autorizacion se valida en servidor."
    ),
    "version": "19.0.1.2.2",
    "category": "Sales/Point of Sale",
    "author": "VLUX",
    "website": "https://vlux.com.mx",
    "license": "LGPL-3",
    "depends": ["point_of_sale", "stock", "vlux_core"],
    "data": [
        "security/security.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "vlux_pos_catalog/static/src/pos/**/*.js",
            "vlux_pos_catalog/static/src/pos/**/*.xml",
            "vlux_pos_catalog/static/src/pos/**/*.scss",
        ],
        "web.assets_tests": [
            "vlux_pos_catalog/static/tests/tours/**/*",
        ],
    },
    "installable": True,
    "application": False,
}
